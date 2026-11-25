#include "weather.hpp"
#include <cassert>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>

int main() {
    using namespace farm;
    unsigned adc[]={1210,1208,3900,1212,1211};
    assert(median5(adc)==1211);
    assert(adc[2]==3900); // filter must not modify the caller's raw samples
    const unsigned resistances[]={33000,8200,1000,2200,3900,16000,120000,64900};
    for(unsigned i=0;i<8;++i) {
        const unsigned code=unsigned(std::lround(4095.0*resistances[i]/(10000+resistances[i])));
        unsigned direction=999;
        assert(DirectionLookup::decode(code,direction));
        assert(direction==45*i);
    }
    unsigned angle=999;
    for(unsigned code: {0u,1u,1400u,4094u,4095u})assert(!DirectionLookup::decode(code,angle));
    DirectionAccumulator north;
    north.add(359);north.add(1);
    assert(north.mean(angle) && (angle==0 || angle==360));
    DirectionAccumulator opposite;
    opposite.add(0);opposite.add(180);
    assert(!opposite.mean(angle));
    DirectionAccumulator empty;
    assert(!empty.mean(angle));
    north.reset();assert(!north.mean(angle));

    AmbientProcessor ambient;
    ambient.ingest({24,67,1010},true);
    assert(ambient.mask()==(quality::TEMPERATURE|quality::HUMIDITY|quality::PRESSURE));
    ambient.ingest({70,68,1009},true);
    ambient.ingest({25,69,1011},true);
    assert(ambient.temperature.filtered==25); // median prevents 70C display outlier
    assert(ambient.humidity.filtered==68);
    assert(ambient.pressure.filtered==1010);
    ambient.ingest({99,50,1000},true);
    assert(!(ambient.mask()&quality::TEMPERATURE));
    assert(ambient.mask()&(quality::HUMIDITY|quality::PRESSURE));
    assert(ambient.temperature.filtered==25); // last good is diagnostic only
    assert(!ambient.fault());
    ambient.ingest({99,50,1000},true);
    ambient.ingest({99,50,1000},true);
    assert(ambient.temperature.fault && ambient.fault());
    for(unsigned i=0;i<2;++i) ambient.ingest({26,50,1000},true);
    assert(ambient.fault());
    ambient.ingest({26,50,1000},true);
    assert(!ambient.fault());
    ambient.ingest({},false);
    assert(ambient.mask()==0);
    assert(!ambient.temperature.valid);
    assert(ambient.temperature.filtered==26);
    ambient.ingest({std::numeric_limits<float>::quiet_NaN(), 50, 1000},true);
    assert(!(ambient.mask()&quality::TEMPERATURE));

    unsigned mv=0;
    assert(batteryMillivolts(2067,3300,mv));
    assert(std::abs(int(mv)-3331)<5);
    assert(!batteryMillivolts(4095,3300,mv));
    assert(!batteryMillivolts(1500,1900,mv));

    assert(acceptPulse(100,0,false,5));
    assert(!acceptPulse(104,100,true,5));
    assert(acceptPulse(105,100,true,5)); // wind debounce
    assert(!acceptPulse(149,100,true,50));
    assert(acceptPulse(150,100,true,50)); // rain debounce
    assert(acceptPulse(4,UINT32_MAX-2,true,5)); // timer wraps

    Station s;
    for(unsigned i=0;i<3;++i)s.tick(15,0);
    assert(s.status&WIND);
    assert(std::fabs(windSpeed(45,3)-10.0)<0.0001);
    assert(s.gust==45);
    for(unsigned i=0;i<62;++i)s.tick(0,0);
    assert(!(s.status&WIND));
    s.tick(0,18);
    assert(s.status&RAIN);
    assert(std::fabs(rainfall(18)-5.0292)<1e-8);
    for(unsigned i=0;i<600;++i)s.tick(0,0);
    assert(!(s.status&RAIN));
    Station t;
    for(unsigned i=0;i<299;++i)assert(!t.tick(0,0));
    assert(t.tick(0,0));
    assert(t.periodElapsed==300);
    t.finishPeriod();
    assert(t.periodElapsed==0 && t.seconds==300);
    t.temperature(35);
    t.temperatureInvalid(); // invalid sample MUST break a confirmation sequence
    t.temperature(35);t.temperature(35);
    assert(!(t.status&HEAT));
    t.temperature(35);
    assert(t.status&HEAT);
    t.sensorFault(true);assert(t.status&SENSOR);
    t.sensorFault(false);assert(!(t.status&SENSOR));
    std::cout<<"Task 03 sensor processing and fault cases PASS\n";
}
