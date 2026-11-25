#pragma once
#include <stdint.h>
#include "design_config.hpp"

namespace farm {
// Logical sleep model, not an electrical measurement. Interrupt handlers remain
// enabled. Adapter must account for actual wake events and completed scheduled work.
enum class PowerState : uint8_t { Active, Sleep };
struct PowerMonitor {
    PowerState state=PowerState::Active;
    uint32_t since=0;
    uint64_t active_us=0,sleep_us=0;
    uint32_t sleep_entries=0,rtc_wakes=0,pulse_wakes=0,wind_edges=0,rain_edges=0;
    void begin(uint32_t now){since=now;}
    void account(uint32_t now){
        const uint32_t elapsed=now-since;
        // Reject short backward clock jitter; preserve true uint32 wrap.
        if(elapsed>0x7fffffffu)return;
        if(state==PowerState::Sleep)sleep_us+=elapsed;
        else active_us+=elapsed;
        since=now;
    }
    void sleep(uint32_t now){
        if(state==PowerState::Sleep)return;
        account(now);state=PowerState::Sleep;++sleep_entries;
    }
    void wakeRtc(uint32_t now){
        account(now);state=PowerState::Active;++rtc_wakes;
    }
    void wakePulse(uint32_t now,bool wind){
        if(wind)++wind_edges;else ++rain_edges;
        if(state==PowerState::Sleep){account(now);state=PowerState::Active;++pulse_wakes;}
    }
};
// Pure schedule: radio handoff is separate from sensing, and the report's
// budgeted receive windows are a model only until the LoRaWAN task is integrated.
struct SampleSchedule {
    static bool ambient(uint32_t s){return s%design::ambient_period_s==0;}
    static bool direction(uint32_t s){return s%design::direction_period_s==0;}
    static bool battery(uint32_t s){return s%design::normal_period_s==0;}
};
}
