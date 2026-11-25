#include "weather.hpp"
#include "power.hpp"
#include <cassert>
#include <iostream>

int main(){
    using namespace farm;
    Station s;
    unsigned reports=0,ambient=0,direction=0,battery=0,totalRain=0;
    const unsigned expected[]={300,300,300,900,300};
    for(unsigned second=1;second<=2100;++second){
        const bool due=s.tick(3,second%100==0?1:0);
        if(SampleSchedule::ambient(second))++ambient;
        if(SampleSchedule::direction(second))++direction;
        if(SampleSchedule::battery(second)){
            ++battery;s.battery(second<=900?3000:3200);
        }
        if(due){
            assert(reports<5 && s.periodElapsed==expected[reports]);
            assert(s.windPeriod==3*s.periodElapsed);
            assert(s.gust==9);
            totalRain+=s.rainPeriod;
            if(second==900)assert(s.period==900 && s.activePeriod==300 && (s.status&BATTERY));
            if(second==1800)assert(s.period==300 && s.activePeriod==900 && !(s.status&BATTERY));
            ++reports;s.finishPeriod();
        }
    }
    assert(reports==5 && totalRain==21 && ambient==35 && direction==420 && battery==7);
    Station invalid;
    invalid.battery(3000);invalid.battery(3000);invalid.batteryInvalid();
    invalid.battery(3000);invalid.battery(3000);assert(!(invalid.status&BATTERY));
    invalid.battery(3000);assert(invalid.status&BATTERY);
    invalid.battery(3200);invalid.battery(3199);invalid.battery(3200);invalid.battery(3200);
    assert(invalid.status&BATTERY);invalid.battery(3200);assert(!(invalid.status&BATTERY));
    Station early;early.period=900;early.finishPeriod();
    for(unsigned i=0;i<600;++i)assert(!early.tick(0,0));
    early.period=300; // recovery mid-interval must never emit a partial period
    for(unsigned i=601;i<900;++i)assert(!early.tick(0,0));
    assert(early.tick(0,0));early.finishPeriod();assert(early.activePeriod==300);
    PowerMonitor p;p.begin(UINT32_MAX-9);p.sleep(UINT32_MAX-4);p.wakePulse(2,true);
    p.sleep(3);p.wakePulse(5,false);p.wakeRtc(7);p.sleep(8);p.sleep(9);p.account(10);
    assert(p.active_us+p.sleep_us==20 && p.sleep_entries==3 && p.rtc_wakes==1);
    assert(p.wind_edges==1 && p.rain_edges==1 && p.pulse_wakes==2);
    assert(p.sleep_us>0 && p.active_us>0);
    PowerMonitor jitter;jitter.begin(1000);jitter.account(2000);jitter.account(1999);jitter.account(3000);
    assert(jitter.active_us==2000);
    std::cout<<"Task 05 power schedule, period boundaries, interrupt wake accounting PASS\n";
}
