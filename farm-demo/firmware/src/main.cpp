#include <Arduino.h>
#include "weather.hpp"
#include "ambient_sensor.hpp"
#include "sim_pins.hpp"
using namespace farm::sim;

#if !defined(FARM_WOKWI_SIMULATION) || !defined(STM32F103xB)
#error This adapter is for Wokwi Blue Pill only; never flash it as the physical station.
#endif

Uart& WeatherSerial=Serial1;
farm::Station station;
farm::AmbientProcessor atmosphere;
farm::DirectionAccumulator directionPeriod;
volatile unsigned tips=0,windPulses=0;
volatile uint32_t lastTip=0,lastWind=0;
volatile bool seenTip=false,seenWind=false;
farm::SimI2c sensorBus(sda,scl);
farm::AmbientSensor ambient(sensorBus);
unsigned sequence=0,prevStatus=0,totalTips=0;
uint32_t nextTick;
unsigned batteryMv=3200;
bool batteryValid=true, directionValid=false;
constexpr uint32_t TICK_MS=tick_ms;
constexpr uint32_t EPOCH=1795453200UL;
// Wokwi ADC uses fixed simulated Vref. Production target must measure VREFINT.
constexpr unsigned SIM_VREF_MV=3300;

void rainISR(){
    const uint32_t now=micros();
    if(farm::acceptPulse(now,lastTip,seenTip,farm::design::rain_debounce_ms*1000/acceleration)){
        ++tips;lastTip=now;seenTip=true;
    }
}
void windISR(){
    const uint32_t now=micros();
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
    farm::AmbientSample sample={};
    const bool readOk=ambient.read(sample);
    atmosphere.ingest(sample,readOk);
    station.sensorFault(atmosphere.fault());
    if(atmosphere.temperature.valid)station.temperature(sample.temperature);
    else station.temperatureInvalid();
    if(!readOk)WeatherSerial.println("BME280_SAMPLE_INVALID");
}
void sampleDirection(){
    unsigned angle=0;
    directionValid=farm::DirectionLookup::decode(readMedian(direction),angle);
    if(directionValid)directionPeriod.add(angle);
}
void sampleBattery(){
    unsigned measured=0;
    batteryValid=farm::batteryMillivolts(readMedian(battery),SIM_VREF_MV,measured);
    if(batteryValid){batteryMv=measured;station.battery(measured);}
    else station.batteryInvalid();
}
void emitTelemetry(){
    unsigned angle=0;
    const bool directionUsable=directionValid && station.windPeriod>0 && directionPeriod.mean(angle);
    unsigned valid=farm::quality::WIND|farm::quality::RAIN;
    valid|=atmosphere.mask();
    if(directionUsable)valid|=farm::quality::DIRECTION;
    if(batteryValid)valid|=farm::quality::BATTERY;

    auto& s=WeatherSerial;
    s.print("TELEMETRY {\"device_id\":\"FARM_WS_01\",\"sequence_number\":");
    s.print(++sequence);
    s.print(",\"ts_s\":");s.print(EPOCH+station.seconds);
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
    s.print(",\"wind_speed\":");
    s.print(farm::windSpeed(station.windPeriod,station.periodElapsed),2);
    s.print(",\"wind_gust\":");
    s.print(farm::windSpeed(station.gust,farm::design::wind_window_s),2);
    if(directionUsable){s.print(",\"wind_direction\":");s.print(angle);}
    s.print(",\"rainfall\":");s.print(farm::rainfall(station.rainPeriod),4);
    if(batteryValid){s.print(",\"battery_voltage\":");s.print(batteryMv/1000.0,3);}
    s.print(",\"status\":");s.print(station.status);
    s.print(",\"valid_mask\":");s.print(valid);s.println("}");
    directionPeriod.reset();
    station.finishPeriod();
}
void setup(){
    WeatherSerial.setRx(uart_rx);WeatherSerial.setTx(uart_tx);
    WeatherSerial.begin(115200);
    analogReadResolution(12);
    pinMode(rain,INPUT_PULLUP);pinMode(wind,INPUT_PULLUP);
    pinMode(buzzer,OUTPUT);pinMode(relay,OUTPUT);
    digitalWrite(buzzer,LOW);digitalWrite(relay,LOW);
    attachInterrupt(digitalPinToInterrupt(rain),rainISR,FALLING);
    attachInterrupt(digitalPinToInterrupt(wind),windISR,FALLING);
    WeatherSerial.println("BOOT FARM_WS_01");
    if(!ambient.begin()){
        WeatherSerial.println("HARDWARE_ERROR BME280_INIT");
        for(;;)delay(1000);
    }
    WeatherSerial.println("BME280_I2C_READY");
    noInterrupts();tips=0;windPulses=0;interrupts();
    readAmbient();sampleDirection();
    nextTick=millis()+TICK_MS;
    WeatherSerial.println("READY FARM_WS_01 SIMULATION");
}
void loop(){
    if((int32_t)(millis()-nextTick)<0)return;
    nextTick+=TICK_MS;
    noInterrupts();const unsigned n=tips,pulses=windPulses;tips=0;windPulses=0;interrupts();
    if(n){totalTips+=n;WeatherSerial.print("RAIN_TIPS=");WeatherSerial.println(totalTips);}
    const bool due=station.tick(pulses,n);
    if(station.seconds%farm::design::ambient_period_s==0)readAmbient();
    if(station.seconds%farm::design::direction_period_s==0)sampleDirection();
    if(station.seconds%farm::design::normal_period_s==0)sampleBattery();
    if((station.status^prevStatus)&farm::WIND)
        WeatherSerial.println(station.status&farm::WIND?"WIND_ALARM_ON":"WIND_ALARM_OFF");
    prevStatus=station.status;
    digitalWrite(buzzer,(station.status&farm::WIND)?HIGH:LOW);
    digitalWrite(relay,(station.status&farm::WIND)?HIGH:LOW);
    if(due)emitTelemetry();
}
