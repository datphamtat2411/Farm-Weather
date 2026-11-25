#include "weather.hpp"
#include <cassert>
#include <iostream>
int main(){
    using namespace farm;
    Station s; LocalActuation a;
    auto tick=[&](unsigned n,bool valid=true,bool confirm=false){
        s.tick(n,0,valid);a.update(s.seconds,s.status,s.windReady,confirm);
    };
    assert(!a.buzzer&&!a.stopRequest);
    tick(15);tick(15);assert(!a.stopRequest);tick(15);
    assert(s.status&WIND);assert(a.mode==LocalMode::Alarm&&a.buzzer&&a.stopRequest);
    tick(15);assert(a.buzzer);tick(15);assert(!a.buzzer&&a.stopRequest);
    // Even a confirmation during danger cannot release the relay.
    tick(15,true,true);assert(a.stopRequest);
    for(int i=0;i<100;++i)tick(0,false,true);
    assert((s.status&WIND)&&a.stopRequest&&!s.windReady);
    tick(0);tick(0);assert(s.quiet==0);tick(0);assert(s.quiet==1);
    for(int i=0;i<58;++i)tick(0);
    assert(s.quiet==59&&a.stopRequest);
    tick(0,true,true);assert(!(s.status&WIND)&&!a.stopRequest&&!a.buzzer); // a simultaneous press cannot skip inspection
    assert(a.mode==LocalMode::AwaitInspection);
    tick(0,false,true);assert(a.mode==LocalMode::AwaitInspection);
    tick(0);tick(0);tick(0,true,true);assert(a.mode==LocalMode::Normal);
    for(int i=0;i<3;++i)tick(15);
    assert(a.stopRequest&&a.buzzer);
    Station rain;
    rain.tick(0,17);assert(!(rain.status&RAIN));
    rain.tick(0,1);assert(rain.status&RAIN);
    for(int i=0;i<599;++i)rain.tick(0,0);
    assert(!(rain.status&RAIN)); // oldest 17 expire, 1 remains
    LocalActuation advisory;
    advisory.update(10,RAIN|HEAT|SENSOR|BATTERY,true);
    assert(!advisory.buzzer&&!advisory.stopRequest);
    Station heat;
    heat.temperature(35);heat.temperature(35);heat.temperatureInvalid();
    heat.temperature(35);heat.temperature(35);assert(!(heat.status&HEAT));
    heat.temperature(35);assert(heat.status&HEAT);
    heat.temperature(32);heat.temperature(32);heat.temperature(33);assert(heat.status&HEAT);
    for(int i=0;i<3;++i)heat.temperature(32);
    assert(!(heat.status&HEAT));
    ReadHealth health;
    health.sample(false);health.sample(false);assert(!health.fault);
    health.sample(false);assert(health.fault);
    health.sample(true);health.sample(true);assert(health.fault);
    health.sample(false);health.sample(true);health.sample(true);assert(health.fault);
    health.sample(true);assert(!health.fault);
    assert(!s.windPeriodValid);s.finishPeriod();assert(s.windPeriodValid);
    std::cout<<"Task 04 local alarms, hysteresis, invalid wind and interlock PASS\n";
}
