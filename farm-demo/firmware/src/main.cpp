#include <Arduino.h>
#include "weather.hpp"

Uart& WeatherSerial = Serial1;
farm::Station station;
volatile unsigned tips=0;
volatile uint32_t lastTip=0;
volatile bool seenTip=false;
unsigned sequence=0, prevStatus=0, totalTips=0;
uint32_t nextTick;
double temperature=0, humidity=0, pressure=0;
unsigned batteryMv=3200;
constexpr uint32_t TICK_MS=100;
constexpr uint32_t EPOCH=1795453200UL;

void rainISR(){
    uint32_t now=millis();
    if(farm::acceptPulse(now,lastTip,seenTip,50)){
        ++tips;lastTip=now;seenTip=true;
    }
}
unsigned readMedian(uint32_t pin){
    unsigned a[5];
    for(unsigned i=0;i<5;++i)a[i]=analogRead(pin);
    return farm::median5(a);
}
double fraction(uint32_t pin){return readMedian(pin)/4095.0;}
void readAmbient(){
    temperature=10+40*fraction(PA2);
    humidity=100*fraction(PA3);
    pressure=900+200*fraction(PA4);
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
    WeatherSerial.setRx(PA10);
    WeatherSerial.setTx(PA9);
    WeatherSerial.begin(115200);
    analogReadResolution(12);
    pinMode(PB1,INPUT_PULLUP);
    pinMode(PB8,OUTPUT);pinMode(PB9,OUTPUT);
    digitalWrite(PB8,LOW);digitalWrite(PB9,LOW);
    attachInterrupt(digitalPinToInterrupt(PB1),rainISR,FALLING);
    readAmbient();
    nextTick=millis()+TICK_MS;
    WeatherSerial.println("READY FARM_WS_01 SIMULATION");
}
void loop(){
    if((int32_t)(millis()-nextTick)<0)return;
    nextTick+=TICK_MS;
    noInterrupts();unsigned n=tips;tips=0;interrupts();
    if(n){totalTips+=n;WeatherSerial.print("RAIN_TIPS=");WeatherSerial.println(totalTips);}
    unsigned pulses=(unsigned)(fraction(PA5)*30.0+0.5);
    bool due=station.tick(pulses,n);
    if(station.seconds%60==0)readAmbient();
    if(station.seconds%300==0){
        batteryMv=2700+(unsigned)(fraction(PA1)*900);
        station.battery(batteryMv);
    }
    if((station.status^prevStatus)&farm::WIND)
        WeatherSerial.println(station.status&farm::WIND?"WIND_ALARM_ON":"WIND_ALARM_OFF");
    prevStatus=station.status;
    digitalWrite(PB8,(station.status&farm::WIND)?HIGH:LOW);
    digitalWrite(PB9,(station.status&farm::WIND)?HIGH:LOW);
    if(due)emitTelemetry();
}
