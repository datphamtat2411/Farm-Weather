#pragma once
#include <stddef.h>
#include <stdint.h>
#include <math.h>
#include "acquisition.hpp"

namespace farm::wire {
constexpr uint8_t PERIODIC_PORT=10, EVENT_PORT=11, ACK_PORT=20, CONFIG_PORT=21;
// Fixed capacity, no heap, no packed struct ABI dependency. The application
// transport must additionally check the current regional/data-rate limit.
constexpr size_t capacity=64;
struct Payload {
    uint8_t data[capacity]={};
    size_t size=0;
    bool ok=true;
    bool byte(uint8_t v) {
        if(!ok || size==capacity){ok=false;return false;}
        data[size++]=v;return true;
    }
    bool varint(uint32_t v) {
        while(v>=128){if(!byte(uint8_t((v&127)|128)))return false;v>>=7;}
        return byte(uint8_t(v));
    }
    bool field(uint8_t tag,uint32_t v) {
        return varint(uint32_t(tag)<<3)&&varint(v);
    }
    bool signedField(uint8_t tag,int32_t v) {
        // Unsigned operations avoid undefined behavior for negative values.
        return field(tag,(uint32_t(v)<<1)^(v<0?UINT32_MAX:0u));
    }
};
inline bool period(uint32_t n){return n==design::normal_period_s||n==design::low_power_period_s;}
inline int32_t x10(double v){return int32_t(round(v*10.0));}
struct Record {
    uint32_t sequence=0,ts=0,period_s=0;
    int32_t temperature=0;
    uint32_t humidity=0,pressure=0,wind=0,gust=0,direction=0,rain=0,battery=0,status=0,valid=0;
};
inline Payload encode(const Record& r) {
    Payload p;
    if(!r.sequence||!r.ts||!period(r.period_s)||(r.status&~31u)||(r.valid&~quality::ALL)||
       ((r.valid&quality::TEMPERATURE)&&(r.temperature<-400||r.temperature>850))||
       ((r.valid&quality::HUMIDITY)&&r.humidity>1000)||
       ((r.valid&quality::PRESSURE)&&(r.pressure<3000||r.pressure>11000))||
       ((r.valid&quality::DIRECTION)&&r.direction>=360)||
       ((r.valid&quality::BATTERY)&&(r.battery<2000||r.battery>3650))){p.ok=false;return p;}
    p.field(1,r.sequence);p.field(2,r.ts);p.field(3,r.period_s);
    if(r.valid&quality::TEMPERATURE)p.signedField(4,r.temperature);
    if(r.valid&quality::HUMIDITY)p.field(5,r.humidity);
    if(r.valid&quality::PRESSURE)p.field(6,r.pressure);
    if(r.valid&quality::WIND){p.field(7,r.wind);p.field(8,r.gust);}
    if(r.valid&quality::DIRECTION)p.field(9,r.direction);
    if(r.valid&quality::RAIN)p.field(10,r.rain);
    if(r.valid&quality::BATTERY)p.field(11,r.battery);
    p.field(12,r.status);p.field(13,r.valid);
    return p;
}
enum class Mode:uint32_t { Normal=0, Alarm=1, AwaitInspection=2 };
struct Event {
    uint32_t sequence=0,ts=0,changed=0,status=0,requested_period=0,fault=0,changed_fault=0;
    Mode mode=Mode::Normal;
    bool buzzer=false,stop=false,has_wind=false,has_temp=false,has_battery=false;
    uint32_t wind=0,rain=0,battery=0;
    int32_t temperature=0;
};
inline Payload encode(const Event& e) {
    Payload p;
    if(!e.sequence||!e.ts||!period(e.requested_period)||((e.status|e.changed)&~31u)||
       ((e.fault|e.changed_fault)&~quality::ALL)||uint32_t(e.mode)>2||
       (e.has_temp&&(e.temperature<-400||e.temperature>850))||
       (e.has_battery&&(e.battery<2000||e.battery>3650))){p.ok=false;return p;}
    p.field(1,e.sequence);p.field(2,e.ts);p.field(3,e.changed);p.field(4,e.status);
    p.field(5,e.requested_period);p.field(6,e.fault);p.field(7,e.changed_fault);
    p.field(8,uint32_t(e.mode));p.field(9,e.buzzer);p.field(10,e.stop);
    if(e.has_wind)p.field(11,e.wind);
    p.field(12,e.rain);
    if(e.has_temp)p.signedField(13,e.temperature);
    if(e.has_battery)p.field(14,e.battery);
    return p;
}
} // namespace farm::wire
