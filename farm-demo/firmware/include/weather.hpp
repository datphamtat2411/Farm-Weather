#pragma once
#include <stdint.h>
#include "design_config.hpp"
#include "acquisition.hpp"

namespace farm {
constexpr unsigned WIND = 1, RAIN = 2, HEAT = 4, BATTERY = 8, SENSOR = 16;
inline double rainfall(unsigned tips) { return tips * design::rain_mm_per_tip; }
inline double windSpeed(unsigned pulses, unsigned seconds) {
    return seconds ? (2.0 * pulses / (3.0 * seconds)) : 0;
}
inline bool acceptPulse(uint32_t now, uint32_t previous, bool seen, uint32_t debounce) {
    return !seen || uint32_t(now - previous) >= debounce;
}
// Health for scheduled non-ambient reads; absence of dry-contact pulses alone
// is not a fault. Only explicit invalid measurements count.
struct ReadHealth {
    unsigned bad=0,good=0;
    bool fault=false;
    void sample(bool valid) {
        if(valid){bad=0;if(good<design::confirmation_count)++good;
            if(good>=design::confirmation_count)fault=false;}
        else {good=0;if(bad<design::confirmation_count)++bad;
            if(bad>=design::confirmation_count)fault=true;}
    }
};
enum class LocalMode { Normal, Alarm, AwaitInspection };
struct LocalActuation {
    LocalMode mode=LocalMode::Normal;
    uint32_t alarmStarted=0;
    bool buzzer=false,stopRequest=false;
    // Nonblocking two seconds on / one second off, logical time.
    void update(uint32_t now,unsigned status,bool windReady,bool confirm=false) {
        const bool alreadyAwaiting=mode==LocalMode::AwaitInspection;
        if(status&WIND){
            if(mode!=LocalMode::Alarm)alarmStarted=now;
            mode=LocalMode::Alarm;
        } else if(mode==LocalMode::Alarm && windReady) mode=LocalMode::AwaitInspection;
        if(alreadyAwaiting && mode==LocalMode::AwaitInspection && confirm && windReady && !(status&WIND))
            mode=LocalMode::Normal;
        stopRequest=mode==LocalMode::Alarm;
        buzzer=stopRequest && (uint32_t(now-alarmStarted)%3<2);
    }
};
struct Station {
    uint32_t seconds=0;
    unsigned status=0, wind[design::wind_window_s]={}, rain[design::rain_window_s]={};
    unsigned rainWindow=0, rainPeriod=0, windPeriod=0, gust=0, periodElapsed=0;
    bool windQuality[design::wind_window_s]={},windReady=false,windPeriodValid=true;
    unsigned currentWindPulses=0;
    unsigned quiet=0, hot=0, cool=0, low=0, charged=0;
    unsigned period=design::normal_period_s; // requested mode, confirmed by battery policy
    unsigned configuredPeriod=design::normal_period_s, windOn=10, windOff=7;
    void configure(unsigned on,unsigned off,unsigned requested){
        if(windOn!=on||windOff!=off)quiet=0;
        windOn=on;windOff=off;configuredPeriod=requested;
        period=(status&BATTERY)?design::low_power_period_s:configuredPeriod;
    }
    unsigned activePeriod=design::normal_period_s; // immutable until current record closes
    bool tick(unsigned windPulses,unsigned rainTips,bool windValid=true) {
        const unsigned i=seconds%design::rain_window_s;
        rainWindow-=rain[i];rain[i]=rainTips;rainWindow+=rainTips;
        wind[seconds%design::wind_window_s]=windValid?windPulses:0;
        windQuality[seconds%design::wind_window_s]=windValid;
        windPeriodValid=windPeriodValid&&windValid;
        ++seconds;++periodElapsed;
        rainPeriod+=rainTips;if(windValid)windPeriod+=windPulses;
        unsigned sum=wind[0]+wind[1]+wind[2];
        windReady=seconds>=design::wind_window_s && windQuality[0] && windQuality[1] && windQuality[2];
        currentWindPulses=sum;
        if(!windReady)quiet=0; // missing data cannot clear an existing warning
        if(windReady) {
            if(sum>gust)gust=sum;
            if(sum*2>=windOn*9){status|=WIND;quiet=0;}
            else if(status&WIND){
                if(sum*2<windOff*9){if(++quiet>=design::wind_recovery_s){status&=~WIND;quiet=0;}}
                else quiet=0;
            }
        }
        if(rainfall(rainWindow)>=design::rain_on_mm)status|=RAIN;
        else if(rainfall(rainWindow)<design::rain_off_mm)status&=~RAIN;
        return periodElapsed>=activePeriod;
    }
    void temperature(double c) {
        hot=c>=design::heat_on_c?hot+1:0;
        cool=c<design::heat_off_c?cool+1:0;
        if(hot>=design::confirmation_count)status|=HEAT;
        if(cool>=design::confirmation_count)status&=~HEAT;
    }
    void temperatureInvalid() { hot=cool=0; }
    void sensorFault(bool active) { if(active)status|=SENSOR;else status&=~SENSOR; }
    void battery(unsigned mv) {
        low=mv<=design::battery_low_mv?low+1:0;
        charged=mv>=design::battery_recovery_mv?charged+1:0;
        if(low>=design::confirmation_count){status|=BATTERY;period=design::low_power_period_s;}
        if(charged>=design::confirmation_count){status&=~BATTERY;period=configuredPeriod;}
    }
    void batteryInvalid() {low=charged=0;}
    void finishPeriod(){rainPeriod=windPeriod=gust=periodElapsed=0;windPeriodValid=true;activePeriod=period;}
};
}
