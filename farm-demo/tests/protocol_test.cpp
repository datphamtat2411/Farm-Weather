#include "wire.hpp"
#include <cassert>
#include <iomanip>
#include <iostream>
using namespace farm::wire;
void emit(const char* name,unsigned port,const Payload& p){
    assert(p.ok && p.size>0 && p.size<=capacity);
    std::cout<<name<<" "<<port<<" ";
    for(size_t i=0;i<p.size;++i)std::cout<<std::hex<<std::setw(2)<<std::setfill('0')<<unsigned(p.data[i]);
    std::cout<<std::dec<<"\n";
}
int main(){
    Record r{1052,1795453200,300,295,782,10084,63,88,135,9,3200,0,127};
    const auto example=encode(r);assert(example.size==37);
    emit("report",PERIODIC_PORT,example);
    r.sequence=1053;r.period_s=900;r.temperature=-125;r.humidity=0;r.wind=0;r.gust=0;r.direction=0;r.rain=0;
    emit("negative_zero",PERIODIC_PORT,encode(r));
    r.sequence=1054;r.valid=0;r.status=16;
    emit("invalid",PERIODIC_PORT,encode(r));
    r.valid=127;r.temperature=-400;r.humidity=1000;r.pressure=11000;r.wind=UINT32_MAX;r.gust=UINT32_MAX;
    r.direction=359;r.rain=UINT32_MAX;r.battery=3650;r.sequence=UINT32_MAX;r.ts=UINT32_MAX;
    emit("bounds",PERIODIC_PORT,encode(r));
    r.period_s=301;assert(!encode(r).ok);r.period_s=300;r.direction=360;assert(!encode(r).ok);
    r.direction=0;r.sequence=0;assert(!encode(r).ok);
    Event e{};e.sequence=1055;e.ts=1795453200;e.changed=3;e.status=3;e.requested_period=300;
    e.mode=Mode::Alarm;e.buzzer=true;e.stop=true;e.has_wind=true;e.wind=100;e.rain=18;
    e.has_temp=true;e.temperature=-125;e.has_battery=true;e.battery=3200;
    emit("event",EVENT_PORT,encode(e));
    e.sequence++;e.changed=0;e.status=0;e.mode=Mode::AwaitInspection;e.buzzer=false;e.stop=false;
    e.has_wind=false;e.has_temp=false;e.has_battery=false;e.rain=0;
    emit("event_missing",EVENT_PORT,encode(e));
    e.mode=static_cast<Mode>(3);assert(!encode(e).ok);
    Payload b;for(size_t i=0;i<capacity;++i)assert(b.byte(0));assert(!b.byte(0)&&!b.ok&&b.size==capacity);
    assert(x10(-12.55)==-126);assert(x10(6.26)==63);
}
