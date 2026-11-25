#include "weather.hpp"
#include <cassert>
#include <cmath>
#include <iostream>
int main(){
    using namespace farm;
    assert(std::abs(rainfall(18)-5.0292)<1e-8);
    assert(std::abs(windSpeed(45,3)-10)<1e-8);
    assert(acceptPulse(0,0,false,50));
    assert(!acceptPulse(30,0,true,50));
    assert(acceptPulse(50,0,true,50));
    unsigned a[]={1210,1208,3900,1212,1211};assert(median5(a)==1211);
    Station s;
    for(int i=0;i<3;i++)s.tick(15,0);
    assert(s.status&WIND);
    for(int i=0;i<59;i++)s.tick(0,0);
    assert(s.status&WIND);
    for(int i=0;i<3;i++)s.tick(0,0);
    assert(!(s.status&WIND));
    s.tick(0,18);assert(s.status&RAIN);
    for(int i=0;i<600;i++)s.tick(0,0);
    assert(!(s.status&RAIN));
    for(int i=0;i<3;i++)s.temperature(35);
    assert(s.status&HEAT);
    for(int i=0;i<3;i++)s.temperature(32);
    assert(!(s.status&HEAT));
    for(int i=0;i<3;i++)s.battery(3000);
    assert(s.period==900 && (s.status&BATTERY));
    for(int i=0;i<3;i++)s.battery(3200);
    assert(s.period==300 && !(s.status&BATTERY));
    assert(acceptPulse(4, UINT32_MAX-2, true, 5));
    assert(!acceptPulse(1, UINT32_MAX-2, true, 5));
    Station timing;
    for(int i=0;i<299;++i) assert(!timing.tick(0,0));
    assert(timing.tick(0,0));
    timing.finishPeriod();
    assert(timing.periodElapsed==0 && timing.seconds==300);
    Station boundary;
    for(int i=0;i<3;++i) boundary.tick(14,0);
    assert(!(boundary.status&WIND));
    boundary.tick(17,0);
    assert(boundary.status&WIND);
    for(int i=0;i<3;++i) boundary.tick(11,0);
    assert(boundary.status&WIND);
    for(int i=0;i<59;++i) boundary.tick(0,0);
    assert(boundary.status&WIND);
    boundary.tick(0,0);
    boundary.tick(0,0);
    assert(!(boundary.status&WIND));
    std::cout<<"Native weather logic PASS\n";
}
