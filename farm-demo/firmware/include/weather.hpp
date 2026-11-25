#pragma once
#include <stdint.h>

namespace farm {
constexpr unsigned WIND = 1, RAIN = 2, HEAT = 4, BATTERY = 8;
inline double rainfall(unsigned tips) { return tips * 0.2794; }
inline double windSpeed(unsigned pulses, unsigned seconds) {
    return seconds ? (2.0 * pulses / (3.0 * seconds)) : 0;
}
inline bool acceptPulse(uint32_t now, uint32_t previous, bool seen, uint32_t debounce) {
    return !seen || uint32_t(now - previous) >= debounce;
}
inline unsigned median5(unsigned* a) {
    for (unsigned i=1;i<5;++i) {
        unsigned v=a[i], j=i;
        while(j && a[j-1]>v){a[j]=a[j-1];--j;}
        a[j]=v;
    }
    return a[2];
}
struct Station {
    uint32_t seconds=0;
    unsigned status=0, wind[3]={}, rain[600]={};
    unsigned rainWindow=0, rainPeriod=0, windPeriod=0, gust=0, periodElapsed=0;
    unsigned quiet=0, hot=0, cool=0, low=0, charged=0;
    unsigned period=300;
    bool tick(unsigned windPulses,unsigned rainTips) {
        unsigned i=seconds%600;
        rainWindow-=rain[i];rain[i]=rainTips;rainWindow+=rainTips;
        wind[seconds%3]=windPulses;
        ++seconds;++periodElapsed;
        rainPeriod+=rainTips;windPeriod+=windPulses;
        unsigned sum=wind[0]+wind[1]+wind[2];
        if(seconds>=3) {
            if(sum>gust)gust=sum;
            if(sum>=45){status|=WIND;quiet=0;}
            else if(status&WIND){
                if(sum*2<63){if(++quiet>=60){status&=~WIND;quiet=0;}}
                else quiet=0;
            }
        }
        if(rainfall(rainWindow)>=5.0)status|=RAIN;
        else if(rainfall(rainWindow)<2.0)status&=~RAIN;
        return periodElapsed>=period;
    }
    void temperature(double c) {
        hot=c>=35?hot+1:0;cool=c<33?cool+1:0;
        if(hot>=3)status|=HEAT;
        if(cool>=3)status&=~HEAT;
    }
    void battery(unsigned mv) {
        low=mv<=3000?low+1:0;charged=mv>=3200?charged+1:0;
        if(low>=3){status|=BATTERY;period=900;}
        if(charged>=3){status&=~BATTERY;period=300;}
    }
    void finishPeriod(){rainPeriod=windPeriod=gust=periodElapsed=0;}
};
}
