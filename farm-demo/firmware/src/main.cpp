#include <Arduino.h>
#include "weather.hpp"
#include "ambient_sensor.hpp"
#include "sim_pins.hpp"
using namespace farm::sim;

#if !defined(FARM_WOKWI_SIMULATION) || !defined(STM32F103xB)
#error This adapter is for Wokwi Blue Pill only; never flash it as the physical station.
#endif

Uart& WeatherSerial = Serial1;
farm::Station station;
volatile unsigned tips=0, windPulses=0;
volatile uint32_t lastTip=0;
volatile bool seenTip=false, seenWind=false;
volatile uint32_t lastWind=0;
farm::AmbientSensor ambient;
TwoWire sensorBus(sda,scl);
unsigned sequence=0, prevStatus=0, totalTips=0;
uint32_t nextTick;
double temperature=0, humidity=0, pressure=0;
unsigned batteryMv=3200;
// Accelerated simulation adapter only, not the physical RTC or power scheduler.
constexpr uint32_t TICK_MS=tick_ms;
constexpr uint32_t EPOCH=1795453200UL;

void rainISR(){
    uint32_t now=micros();
    if(farm::acceptPulse(now,lastTip,seenTip,farm::design::rain_debounce_ms*1000/acceleration)){
        ++tips;lastTip=now;seenTip=true;
    }
}
void windISR(){
    uint32_t now=micros();
    if(farm::acceptPulse(now,lastWind,seenWind,farm::design::wind_debounce_ms*1000/acceleration)){
        ++windPulses;lastWind=now;seenWind=true;
    }
}
unsigned readMedian(uint32_t pin){
    unsigned a[5];
    for(unsigned i=0;i<5;++i)a[i]=analogRead(pin);
    return farm::median5(a);
}
double fraction(uint32_t pin){return readMedian(pin)/4095.0;}
void readAmbient(){
    farm::AmbientSample sample;
    if(!ambient.read(sample)){
        WeatherSerial.println("HARDWARE_ERROR BME280_READ");
        return;
    }
    temperature=sample.temperature;humidity=sample.humidity;pressure=sample.pressure;
    station.temperature(temperature);
}
void emitTelemetry(){
    auto& s=WeatherSerial;
    s.print("TELEMETRY {\"device_id\":\"FARM_WS_01\",\"sequence_number\":");
    s.print(++sequence);
    s.print(",\"ts_s\":");s.print(EPOCH+station.seconds);
    s.print(",\"period_s\":");s.print(station.periodElapsed);
    s.print(",\"temperature\":");s.print(temperature,1);
    s.print(",\"humidity\":");s.print(humidity,1);
    s.print(",\"pressure\":");s.print(pressure,1);
    s.print(",\"wind_speed\":");s.print(farm::windSpeed(station.windPeriod,station.periodElapsed),2);
    s.print(",\"wind_gust\":");s.print(farm::windSpeed(station.gust,3),2);
    s.print(",\"wind_direction\":");s.print((unsigned)(fraction(PA0)*8.0+0.5)%8*45);
    s.print(",\"rainfall\":");s.print(farm::rainfall(station.rainPeriod),4);
    s.print(",\"battery_voltage\":");s.print(batteryMv/1000.0,3);
    s.print(",\"status\":");s.print(station.status);
    s.println(",\"valid_mask\":127}");
    station.finishPeriod();
}
void setup(){
    WeatherSerial.setRx(uart_rx);
    WeatherSerial.setTx(uart_tx);
    WeatherSerial.begin(115200);
    analogReadResolution(12);
    pinMode(rain,INPUT_PULLUP);pinMode(wind,INPUT_PULLUP);
    pinMode(buzzer,OUTPUT);pinMode(relay,OUTPUT);
    digitalWrite(buzzer,LOW);digitalWrite(relay,LOW);
    attachInterrupt(digitalPinToInterrupt(rain),rainISR,FALLING);
    attachInterrupt(digitalPinToInterrupt(wind),windISR,FALLING);
    sensorBus.begin();sensorBus.setClock(100000);
    if(!ambient.begin(sensorBus)){
        WeatherSerial.println("HARDWARE_ERROR BME280_INIT");
        for(;;)delay(1000);
    }
    noInterrupts();tips=0;windPulses=0;interrupts();
    readAmbient();
    nextTick=millis()+TICK_MS;
    WeatherSerial.println("READY FARM_WS_01 SIMULATION");
}
void loop(){
    if((int32_t)(millis()-nextTick)<0)return;
    nextTick+=TICK_MS;
    noInterrupts();unsigned n=tips,pulses=windPulses;tips=0;windPulses=0;interrupts();
    if(n){totalTips+=n;WeatherSerial.print("RAIN_TIPS=");WeatherSerial.println(totalTips);}
    bool due=station.tick(pulses,n);
    if(station.seconds%farm::design::ambient_period_s==0)readAmbient();
    if(station.seconds%farm::design::normal_period_s==0){
        batteryMv=2700+(unsigned)(fraction(PA1)*900);
        station.battery(batteryMv);
    }
    if((station.status^prevStatus)&farm::WIND)
        WeatherSerial.println(station.status&farm::WIND?"WIND_ALARM_ON":"WIND_ALARM_OFF");
    prevStatus=station.status;
    digitalWrite(buzzer,(station.status&farm::WIND)?HIGH:LOW);
    digitalWrite(relay,(station.status&farm::WIND)?HIGH:LOW);
    if(due)emitTelemetry();
}
