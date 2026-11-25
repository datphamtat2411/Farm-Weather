#include <Arduino.h>
#include "weather.hpp"
#include "power.hpp"
#include "ambient_sensor.hpp"
#include "sim_pins.hpp"
#include "wire.hpp"
#include "sim_flash.hpp"
#include "class_a.hpp"
#include "commands.hpp"
using namespace farm::sim;

#if !defined(FARM_WOKWI_SIMULATION) || !defined(STM32F103xB)
#error This adapter is for Wokwi Blue Pill only; never flash it as the physical station.
#endif

Uart& WeatherSerial=Serial1;
farm::Station station;
farm::PowerMonitor power;
unsigned ambientSamples=0,directionSamples=0,batterySamples=0;
farm::LocalActuation actuation;
farm::ReadHealth windHealth,directionHealth,batteryHealth;
float latestTemperature=0;
bool ambientReady=false,confirmWasPressed=false;
farm::AmbientProcessor atmosphere;
farm::DirectionAccumulator directionPeriod;
volatile unsigned tips=0,windPulses=0;
volatile uint32_t lastTip=0,lastWind=0;
volatile bool seenTip=false,seenWind=false;
farm::SimI2c sensorBus(sda,scl);
farm::AmbientSensor ambient(sensorBus);
farm::sim::Flash flash(sensorBus);
farm::control::Controller<farm::sim::Flash> controller(flash);
farm::storage::Ring<farm::sim::Flash> ring(flash);
farm::link::ClassA<decltype(ring)> radio(ring);
uint32_t sequence=0;
unsigned prevStatus=0,prevFaultMask=0,totalTips=0;
uint32_t nextTick;
unsigned batteryMv=3200;
bool batteryValid=true, directionValid=false;
constexpr uint32_t TICK_MS=tick_ms;
constexpr uint32_t EPOCH=1795453200UL;
uint32_t clockOffset=0,recordTimestamp=0;
bool clockTrusted=false;
// Wokwi ADC uses fixed simulated Vref. Production target must measure VREFINT.
constexpr unsigned SIM_VREF_MV=3300;

void rainISR(){
    const uint32_t now=micros();
    power.wakePulse(now,false);
    if(farm::acceptPulse(now,lastTip,seenTip,farm::design::rain_debounce_ms*1000/acceleration)){
        ++tips;lastTip=now;seenTip=true;
    }
}
void windISR(){
    const uint32_t now=micros();
    power.wakePulse(now,true);
    if(farm::acceptPulse(now,lastWind,seenWind,farm::design::wind_debounce_ms*1000/acceleration)){
        ++windPulses;lastWind=now;seenWind=true;
    }
}
unsigned readMedian(uint32_t pin){
    unsigned values[5];
    for(unsigned i=0;i<5;++i) values[i]=analogRead(pin);
    return farm::median5(values);
}
void readAmbient(){
    ++ambientSamples;
    farm::AmbientSample sample={};
    if(!ambientReady)ambientReady=ambient.begin();
    const bool readOk=ambientReady&&ambient.read(sample);
    if(!readOk)ambientReady=false;
    atmosphere.ingest(sample,readOk);
    if(atmosphere.temperature.valid){latestTemperature=sample.temperature;station.temperature(sample.temperature);}
    else station.temperatureInvalid();
    if(!readOk)WeatherSerial.println("BME280_SAMPLE_INVALID");
}
void sampleDirection(){
    ++directionSamples;
    unsigned angle=0;
    directionValid=farm::DirectionLookup::decode(readMedian(direction),angle);
    directionHealth.sample(directionValid);
    if(directionValid)directionPeriod.add(angle);
}
void sampleBattery(){
    ++batterySamples;
    unsigned measured=0;
    batteryValid=farm::batteryMillivolts(readMedian(battery),SIM_VREF_MV,measured);
    batteryHealth.sample(batteryValid);
    if(batteryValid){batteryMv=measured;station.battery(measured);}
    else station.batteryInvalid();
}
// Arduino Print does not consistently support uint64_t on STM32.
void printUint64(uint64_t n){
    char buf[21];unsigned i=sizeof(buf);buf[--i]='\0';
    do {buf[--i]=char('0'+n%10);n/=10;}while(n);
    WeatherSerial.print(buf+i);
}
// Hex is only the serial simulation adapter. The wire bytes contain no JSON or device ID.
void appendWire(const farm::wire::Payload& payload,uint8_t port){
    if(!payload.ok || !payload.size){
        WeatherSerial.print(",\"wire_error\":true");
        return;
    }
    static const char digits[]="0123456789abcdef";
    char hex[2*farm::wire::capacity+1];
    for(size_t i=0;i<payload.size;++i){
        hex[2*i]=digits[payload.data[i]>>4];
        hex[2*i+1]=digits[payload.data[i]&15];
    }
    hex[2*payload.size]='\0';
    WeatherSerial.print(",\"fport\":");WeatherSerial.print(port);
    WeatherSerial.print(",\"wire_hex\":\"");WeatherSerial.print(hex);WeatherSerial.print("\"");
}
// Simulation gateway double: accepts delivered uplinks and schedules an explicit
// FPort 20 ACK in RX1. It is not the SQLite commit service (Task 08).
bool linkUp=true,autoAck=true,dropAck=false;
uint32_t mockCommitted=0;
char commandLine[160]={};unsigned commandSize=0;bool commandOverflow=false;
void printHex(const farm::wire::Payload& payload){
    const char digits[]="0123456789abcdef";
    for(size_t i=0;i<payload.size;++i){WeatherSerial.print(digits[payload.data[i]>>4]);WeatherSerial.print(digits[payload.data[i]&15]);}
}
void queueRecord(uint8_t port,const farm::wire::Payload& payload){
    const bool warned=ring.nearFull();
    const auto result=ring.append({sequence,port,payload});
    WeatherSerial.print(result==farm::storage::StoreResult::Stored?"BUFFER_STORED ":"BUFFER_GAP ");
    WeatherSerial.print(sequence);WeatherSerial.print(" pending=");WeatherSerial.print(ring.count);
    WeatherSerial.print(" used=");WeatherSerial.println(ring.usedSlots);
    if(!warned&&ring.nearFull())WeatherSerial.println("BUFFER_WARNING_ON");
    if(result==farm::storage::StoreResult::IoError)WeatherSerial.println("HARDWARE_ERROR FLASH_IO");
}
bool reserveRecord(){
    if(!clockTrusted||!farm::storage::timestamp(EPOCH,clockOffset,station.seconds,recordTimestamp)){
        WeatherSerial.println("BUFFER_GAP CLOCK_INVALID");return false;
    }
    sequence=ring.reserve();
    if(sequence)return true;
    WeatherSerial.println("HARDWARE_ERROR FLASH_SEQUENCE");
    return false;
}
bool safeForConfig(){
    return station.windReady && !(station.status&farm::WIND) && station.currentWindPulses*2<station.windOff*9 && !windHealth.fault;
}
void driveOutputs(){
    digitalWrite(buzzer,(actuation.buzzer||controller.testing(station.seconds))?HIGH:LOW);
    digitalWrite(relay,(actuation.stopRequest||controller.snapshot().stop)?HIGH:LOW);
}
void applyAck(uint8_t port,const farm::wire::Payload& p);
uint32_t lastCommandFrame=0;
void applyCommand(const farm::wire::Payload& p){
    farm::control::Command c;
    if(!radio.commandWindow(station.seconds)||lastCommandFrame==radio.frameCounter||!farm::control::decode(p,c)){
        WeatherSerial.println("RPC_FRAME_REJECTED");return;
    }
    uint32_t now=0;
    const bool trusted=clockTrusted&&farm::storage::timestamp(EPOCH,clockOffset,station.seconds,now);
    lastCommandFrame=radio.frameCounter;
    // A response needs a durable sequence before any side effect.
    if(!reserveRecord()){farm::wire::Payload ack;ack.field(1,c.ack);applyAck(farm::wire::ACK_PORT,ack);return;}
    const auto r=controller.execute(c,now,trusted,safeForConfig(),station.seconds);
    const auto& config=controller.snapshot();
    station.configure(config.on,config.off,config.period);
    driveOutputs();
    const auto result=farm::control::encode(sequence,recordTimestamp,c,r,config,station.period,
        actuation.buzzer||controller.testing(station.seconds),actuation.stopRequest||config.stop);
    queueRecord(farm::control::result_port,result);
    WeatherSerial.print("RPC_RESULT outcome=");WeatherSerial.print(unsigned(r.outcome));
    WeatherSerial.print(" reason=");WeatherSerial.print(unsigned(r.reason));
    WeatherSerial.print(" version=");WeatherSerial.println(config.version);
    // One Class A downlink carries both command and explicit durable data ACK.
    farm::wire::Payload ack;ack.field(1,c.ack);applyAck(farm::wire::ACK_PORT,ack);
}
void applyAck(uint8_t port,const farm::wire::Payload& p){
    const bool warned=ring.nearFull();
    const unsigned window=radio.window(station.seconds);
    const unsigned accepted=radio.downlink(station.seconds,port,p);
    WeatherSerial.print("ACK_FRAME ts=");WeatherSerial.print(station.seconds);
    WeatherSerial.print(" window=");WeatherSerial.print(window);
    WeatherSerial.print(" fport=");WeatherSerial.print(port);
    WeatherSerial.print(" hex=");printHex(p);WeatherSerial.println();
    WeatherSerial.print("DOWNLINK fport=");WeatherSerial.print(port);
    WeatherSerial.print(" rx="); // Window may close after accepting current ACK.
    WeatherSerial.print(accepted?"accepted":"rejected");
    WeatherSerial.print(" count=");WeatherSerial.print(accepted);
    WeatherSerial.print(" pending=");WeatherSerial.println(ring.count);
    if(warned&&!ring.nearFull())WeatherSerial.println("BUFFER_WARNING_OFF");
    if(!ring.ready())WeatherSerial.println("HARDWARE_ERROR FLASH_ACK");
}
int nibble(char c){
    if(c>='0'&&c<='9')return c-'0';
    if(c>='a'&&c<='f')return c-'a'+10;
    return -1;
}
void handleCommand(){
    if(!strcmp(commandLine,"LINK DOWN")){linkUp=false;mockCommitted=0;WeatherSerial.println("LINK_DOWN");}
    else if(!strcmp(commandLine,"LINK UP")){linkUp=true;WeatherSerial.println("LINK_UP");}
    else if(!strcmp(commandLine,"ACK DROP")){dropAck=true;WeatherSerial.println("ACK_DROP");}
    else if(!strcmp(commandLine,"ACK AUTO")){autoAck=true;dropAck=false;WeatherSerial.println("ACK_AUTO");}
    else if(!strcmp(commandLine,"ACK MANUAL")){autoAck=false;mockCommitted=0;WeatherSerial.println("ACK_MANUAL");}
    else if(!strncmp(commandLine,"LIMIT ",6)){
        char* end=nullptr;const unsigned long limit=strtoul(commandLine+6,&end,10);
        if(end&&!*end&&limit>0&&limit<=farm::wire::capacity){radio.payloadLimit=unsigned(limit);WeatherSerial.println("LIMIT_SET");}
        else WeatherSerial.println("COMMAND_REJECTED");
    }else if(!strncmp(commandLine,"DOWNLINK ",9)){
        char* end=nullptr;const unsigned long port=strtoul(commandLine+9,&end,10);
        farm::wire::Payload p;
        if(!end||*end!=' '||port>255){WeatherSerial.println("COMMAND_REJECTED");return;}
        const char* hex=end+1;
        while(*hex){
            const int a=nibble(*hex++);if(!*hex){p.ok=false;break;}
            const int b=nibble(*hex++);
            if(a<0||b<0||!p.byte(uint8_t(a*16+b))){p.ok=false;break;}
        }
        if(linkUp){if(port==farm::wire::CONFIG_PORT)applyCommand(p);else applyAck(uint8_t(port),p);}
    }else WeatherSerial.println("COMMAND_REJECTED");
}
void readCommands(){
    // Bound work per loop, no blocking serial read or dynamic String.
    for(unsigned n=0;n<32&&WeatherSerial.available();++n){
        const char c=char(WeatherSerial.read());
        if(c=='\r')continue;
        if(c=='\n'){
            if(!commandOverflow){commandLine[commandSize]='\0';handleCommand();}
            else WeatherSerial.println("COMMAND_REJECTED");
            commandSize=0;commandOverflow=false;
        }else if(commandSize+1<sizeof(commandLine))commandLine[commandSize++]=c;
        else commandOverflow=true;
    }
}
void serviceRadio(){
    const unsigned rx=radio.window(station.seconds);
    if(rx){
        WeatherSerial.print("RX");WeatherSerial.println(rx);
        if(rx==1&&linkUp&&autoAck&&mockCommitted&&!dropAck){
            farm::wire::Payload ack;ack.field(1,mockCommitted);
            WeatherSerial.print("SIM_GATEWAY_ACK ");WeatherSerial.println(mockCommitted);
            applyAck(farm::wire::ACK_PORT,ack);mockCommitted=0;
        }
    }
    const unsigned corruptBefore=ring.corrupt;
    radio.tick(station.seconds,[](const farm::storage::Frame& f,uint32_t counter){
        WeatherSerial.print("RADIO_TX seq=");WeatherSerial.print(f.sequence);
        WeatherSerial.print(" fcnt=");WeatherSerial.print(counter);
        WeatherSerial.print(" fport=");WeatherSerial.print(f.port);
        WeatherSerial.print(" hex=");printHex(f.payload);
        WeatherSerial.print(" delivered=");WeatherSerial.print(linkUp?1:0);
        WeatherSerial.print(" ts=");WeatherSerial.println(station.seconds);
        mockCommitted=linkUp?f.sequence:0;
    });
    if(ring.corrupt!=corruptBefore){WeatherSerial.print("BUFFER_CORRUPT total=");WeatherSerial.println(ring.corrupt);}
    if(!ring.ready())WeatherSerial.println("HARDWARE_ERROR FLASH_RADIO");
}

void emitTelemetry(){
    if(!reserveRecord()){directionPeriod.reset();station.finishPeriod();return;}
    unsigned angle=0;
    const bool directionUsable=directionValid && station.windPeriodValid && station.windPeriod>0 && directionPeriod.mean(angle);
    unsigned valid=farm::quality::RAIN;
    if(station.windPeriodValid)valid|=farm::quality::WIND;
    valid|=atmosphere.mask();
    if(directionUsable)valid|=farm::quality::DIRECTION;
    if(batteryValid)valid|=farm::quality::BATTERY;

    auto& s=WeatherSerial;
    s.print("TELEMETRY {\"device_id\":\"FARM_WS_01\",\"sequence_number\":");
    s.print(sequence);
    s.print(",\"ts_s\":");s.print(recordTimestamp);
    s.print(",\"period_s\":");s.print(station.periodElapsed);
    if(valid&farm::quality::TEMPERATURE){
        s.print(",\"temperature\":");s.print(atmosphere.temperature.filtered,1);
    }
    if(valid&farm::quality::HUMIDITY){
        s.print(",\"humidity\":");s.print(atmosphere.humidity.filtered,1);
    }
    if(valid&farm::quality::PRESSURE){
        s.print(",\"pressure\":");s.print(atmosphere.pressure.filtered,1);
    }
    if(valid&farm::quality::WIND){
        s.print(",\"wind_speed\":");
        s.print(farm::windSpeed(station.windPeriod,station.periodElapsed),2);
        s.print(",\"wind_gust\":");
        s.print(farm::windSpeed(station.gust,farm::design::wind_window_s),2);
    }
    if(directionUsable){s.print(",\"wind_direction\":");s.print(angle);}
    s.print(",\"rainfall\":");s.print(farm::rainfall(station.rainPeriod),4);
    if(batteryValid){s.print(",\"battery_voltage\":");s.print(batteryMv/1000.0,3);}
    s.print(",\"status\":");s.print(station.status);
    s.print(",\"valid_mask\":");s.print(valid);
    farm::wire::Record record{};
    record.sequence=sequence;record.ts=recordTimestamp;record.period_s=station.periodElapsed;
    record.valid=valid;record.status=station.status;
    if(valid&farm::quality::TEMPERATURE)record.temperature=farm::wire::x10(atmosphere.temperature.filtered);
    if(valid&farm::quality::HUMIDITY)record.humidity=unsigned(farm::wire::x10(atmosphere.humidity.filtered));
    if(valid&farm::quality::PRESSURE)record.pressure=unsigned(farm::wire::x10(atmosphere.pressure.filtered));
    if(valid&farm::quality::WIND){
        record.wind=unsigned(farm::wire::x10(farm::windSpeed(station.windPeriod,station.periodElapsed)));
        record.gust=unsigned(farm::wire::x10(farm::windSpeed(station.gust,farm::design::wind_window_s)));
    }
    record.direction=angle;record.rain=station.rainPeriod;record.battery=batteryMv;
    const auto payload=farm::wire::encode(record);
    appendWire(payload,farm::wire::PERIODIC_PORT);
    // Diagnostics are not part of the compact wire schema.
    s.print(",\"next_period_s\":");s.print(station.period);
    s.print(",\"ambient_samples\":");s.print(ambientSamples);
    s.print(",\"direction_samples\":");s.print(directionSamples);
    s.print(",\"battery_samples\":");s.print(batterySamples);
    // Copy atomically: counters and 64-bit accumulators are shared with EXTI.
    noInterrupts();power.account(micros());const auto stats=power;interrupts();
    s.print(",\"sim_active_us\":");printUint64(stats.active_us);
    s.print(",\"sim_sleep_us\":");printUint64(stats.sleep_us);
    s.print(",\"sleep_entries\":");s.print(stats.sleep_entries);
    s.print(",\"rtc_wakes\":");s.print(stats.rtc_wakes);
    s.print(",\"pulse_wakes\":");s.print(stats.pulse_wakes);
    s.print(",\"wind_edges\":");s.print(stats.wind_edges);
    s.print(",\"rain_edges\":");s.print(stats.rain_edges);
    s.println("}");
    queueRecord(farm::wire::PERIODIC_PORT,payload);
    directionPeriod.reset();
    station.finishPeriod();
}
unsigned faultMask(){
    using namespace farm::quality;
    return (atmosphere.temperature.fault?TEMPERATURE:0u)|
           (atmosphere.humidity.fault?HUMIDITY:0u)|
           (atmosphere.pressure.fault?PRESSURE:0u)|
           (windHealth.fault?WIND:0u)|(directionHealth.fault?DIRECTION:0u)|
           (batteryHealth.fault?BATTERY:0u);
}
const char* modeName(){
    switch(actuation.mode){
        case farm::LocalMode::Alarm:return "ALARM";
        case farm::LocalMode::AwaitInspection:return "AWAIT_INSPECTION";
        default:return "NORMAL";
    }
}
void emitEvent(unsigned changed,unsigned changedFaults){
    if(!reserveRecord())return;
    // One snapshot per logical second, including simultaneous transitions.
    // Shared sequence namespace with periodic records; no periodic rain increment.
    auto& s=WeatherSerial;
    s.print("EVENT {\"device_id\":\"FARM_WS_01\",\"kind\":\"event\",\"sequence_number\":");s.print(sequence);
    s.print(",\"ts_s\":");s.print(recordTimestamp);
    s.print(",\"changed_mask\":");s.print(changed);
    s.print(",\"status\":");s.print(station.status);
    s.print(",\"requested_period_s\":");s.print(station.period);
    s.print(",\"fault_mask\":");s.print(faultMask());
    s.print(",\"changed_fault_mask\":");s.print(changedFaults);
    s.print(",\"mode\":\"");s.print(modeName());s.print("\"");
    s.print(",\"buzzer\":");s.print((actuation.buzzer||controller.testing(station.seconds))?"true":"false");
    s.print(",\"stop_request\":");s.print((actuation.stopRequest||controller.snapshot().stop)?"true":"false");
    if(station.windReady){s.print(",\"wind_window_mps\":");s.print(farm::windSpeed(station.currentWindPulses,3),2);}
    s.print(",\"rain_10min_mm\":");s.print(farm::rainfall(station.rainWindow),4);
    if(atmosphere.temperature.valid){s.print(",\"temperature\":");s.print(latestTemperature,1);}
    if(batteryValid){s.print(",\"battery_voltage\":");s.print(batteryMv/1000.0,3);}
    farm::wire::Event event{};
    event.sequence=sequence;event.ts=recordTimestamp;event.changed=changed;event.status=station.status;
    event.requested_period=station.period;event.fault=faultMask();event.changed_fault=changedFaults;
    switch(actuation.mode){
        case farm::LocalMode::Alarm:event.mode=farm::wire::Mode::Alarm;break;
        case farm::LocalMode::AwaitInspection:event.mode=farm::wire::Mode::AwaitInspection;break;
        default:event.mode=farm::wire::Mode::Normal;break;
    }
    event.buzzer=actuation.buzzer||controller.testing(station.seconds);event.stop=actuation.stopRequest||controller.snapshot().stop;
    event.has_wind=station.windReady;event.has_temp=atmosphere.temperature.valid;event.has_battery=batteryValid;
    if(event.has_wind)event.wind=unsigned(farm::wire::x10(farm::windSpeed(station.currentWindPulses,3)));
    event.rain=station.rainWindow;
    if(event.has_temp)event.temperature=farm::wire::x10(latestTemperature);
    event.battery=batteryMv;
    const auto payload=farm::wire::encode(event);
    appendWire(payload,farm::wire::EVENT_PORT);
    s.println("}");
    queueRecord(farm::wire::EVENT_PORT,payload);
}
void setup(){
    WeatherSerial.setRx(uart_rx);WeatherSerial.setTx(uart_tx);
    WeatherSerial.begin(115200);
    analogReadResolution(12);
    pinMode(rain,INPUT_PULLUP);pinMode(wind,INPUT_PULLUP);
    pinMode(wind_fault,INPUT_PULLDOWN);pinMode(confirm_button,INPUT_PULLUP);
    pinMode(buzzer,OUTPUT);pinMode(relay,OUTPUT);
    digitalWrite(buzzer,LOW);digitalWrite(relay,LOW);
    sensorBus.begin();
    if(!ring.begin())WeatherSerial.println("HARDWARE_ERROR FLASH_RECOVERY");
    if(!controller.begin())WeatherSerial.println("RPC_STORAGE_UNAVAILABLE");
    const auto& config=controller.snapshot();station.configure(config.on,config.off,config.period);
    WeatherSerial.print("FLASH_READY highwater=");WeatherSerial.print(ring.highwater);
    WeatherSerial.print(" pending=");WeatherSerial.print(ring.count);WeatherSerial.print(" corrupt=");WeatherSerial.println(ring.corrupt);
    power.begin(micros());
    attachInterrupt(digitalPinToInterrupt(rain),rainISR,FALLING);
    attachInterrupt(digitalPinToInterrupt(wind),windISR,FALLING);
    WeatherSerial.println("BOOT FARM_WS_01");
    ambientReady=ambient.begin();
    WeatherSerial.println(ambientReady?"BME280_I2C_READY":"BME280_INIT_FAILED");
    noInterrupts();tips=0;windPulses=0;interrupts();
    readAmbient();sampleDirection();
    // The simulator's independent clock continues across MCU reset. A fresh
    // simulation has a fresh NOR image and a new clock anchor.
    clockTrusted=flash.readClock(clockOffset)&&(!ring.highwater||clockOffset>0);
    WeatherSerial.print("SIM_RTC offset=");WeatherSerial.print(clockOffset);
    WeatherSerial.print(" trusted=");WeatherSerial.println(clockTrusted?1:0);
    if(!clockTrusted)WeatherSerial.println("HARDWARE_ERROR CLOCK_INVALID");
    nextTick=millis()+TICK_MS;
    WeatherSerial.println("READY FARM_WS_01 SIMULATION");
}
void loop(){
    readCommands();
    if((int32_t)(millis()-nextTick)<0){
        // Logical Stop 2: no peripheral tasks due, no transactions in flight.
        // Do NOT execute physical Stop 2 on Wokwi F103; SysTick/EXTI stay live.
        noInterrupts();power.sleep(micros());interrupts();
        return;
    }
    nextTick+=TICK_MS;
    noInterrupts();power.wakeRtc(micros());const unsigned n=tips,pulses=windPulses;tips=0;windPulses=0;interrupts();
    if(n){totalTips+=n;WeatherSerial.print("RAIN_TIPS=");WeatherSerial.println(totalTips);}
    const bool windValid=!digitalRead(wind_fault);
    windHealth.sample(windValid);
    const bool due=station.tick(pulses,n,windValid);
    if(farm::SampleSchedule::ambient(station.seconds))readAmbient();
    if(farm::SampleSchedule::direction(station.seconds))sampleDirection();
    if(farm::SampleSchedule::battery(station.seconds))sampleBattery();
    const unsigned faults=faultMask();
    station.sensorFault(faults!=0);
    const bool pressed=!digitalRead(confirm_button);
    const auto before=actuation.mode;
    actuation.update(station.seconds,station.status,station.windReady,pressed&&!confirmWasPressed);
    const bool remoteReleased=pressed&&!confirmWasPressed&&controller.confirm(safeForConfig());
    confirmWasPressed=pressed;
    driveOutputs();
    const unsigned changed=station.status^prevStatus;
    const unsigned changedFaults=prevFaultMask^faults;
    if(changed || changedFaults || before!=actuation.mode || remoteReleased)emitEvent(changed,changedFaults);
    prevFaultMask=faults;
    if(changed&farm::WIND)
        WeatherSerial.println(station.status&farm::WIND?"WIND_ALARM_ON":"WIND_ALARM_OFF");
    prevStatus=station.status;
    if(due)emitTelemetry();
    serviceRadio();
}
