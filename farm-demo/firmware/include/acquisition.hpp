#pragma once
#include <math.h>
#include <stdint.h>
#include "design_config.hpp"

namespace farm {
namespace quality {
constexpr unsigned TEMPERATURE = 1u << 0;
constexpr unsigned HUMIDITY = 1u << 1;
constexpr unsigned PRESSURE = 1u << 2;
constexpr unsigned WIND = 1u << 3;
constexpr unsigned DIRECTION = 1u << 4;
constexpr unsigned RAIN = 1u << 5;
constexpr unsigned BATTERY = 1u << 6;
constexpr unsigned ALL = 0x7fu;
}
struct AmbientSample { float temperature, humidity, pressure; };

inline unsigned median5(const unsigned (&input)[5]) {
    unsigned a[5];
    for (unsigned i = 0; i < 5; ++i) a[i] = input[i];
    for (unsigned i = 1; i < 5; ++i) {
        unsigned v = a[i], j = i;
        while (j && a[j-1] > v) { a[j] = a[j-1]; --j; }
        a[j] = v;
    }
    return a[2];
}

// A channel may retain its last good value for diagnostics, never as a fresh reading.
// Validity concerns the latest scheduled measurement, not the existence of a cached value.
struct Channel {
    double samples[3] = {};
    unsigned count = 0, next = 0;
    unsigned bad = 0, good = 0;
    bool valid = false, fault = false;
    double filtered = 0;
    void ingest(double value, bool ok, double min, double max) {
        valid = ok && isfinite(value) && value >= min && value <= max;
        if (!valid) {
            good = 0;
            if (bad < design::confirmation_count) ++bad;
            if (bad >= design::confirmation_count) fault = true;
            return;
        }
        bad = 0;
        if (good < design::confirmation_count) ++good;
        if (good >= design::confirmation_count) fault = false;
        samples[next] = value;
        next = (next + 1) % 3;
        if (count < 3) ++count;
        if (count < 3) { filtered = value; return; }
        double a = samples[0], b = samples[1], c = samples[2];
        if (a > b) { double t=a; a=b; b=t; }
        if (b > c) { double t=b; b=c; c=t; }
        if (a > b) b=a;
        filtered = b;
    }
};
struct AmbientProcessor {
    Channel temperature, humidity, pressure;
    void ingest(const AmbientSample& r, bool bus_ok) {
        temperature.ingest(r.temperature,bus_ok,-40,85);
        humidity.ingest(r.humidity,bus_ok,0,100);
        pressure.ingest(r.pressure,bus_ok,300,1100);
    }
    unsigned mask() const {
        return (temperature.valid?quality::TEMPERATURE:0u) |
               (humidity.valid?quality::HUMIDITY:0u) |
               (pressure.valid?quality::PRESSURE:0u);
    }
    bool fault() const { return temperature.fault||humidity.fault||pressure.fault; }
};

// 10k pull-up with SEN-15901 vane resistances, eight reliable compass positions.
// Lookup values are derived from SparkFun's resistive vane table. Reject an
// unrecognized, disconnected or ambiguous ADC value rather than rounding 0..4095
// into eight equally sized angular sectors.
struct DirectionLookup {
    struct Position { unsigned degrees; uint32_t ohms; };
    static bool decode(unsigned adc, unsigned& degrees) {
        if (adc == 0 || adc >= 4095) return false;
        constexpr Position table[8] = {
            {0,33000},{45,8200},{90,1000},{135,2200},
            {180,3900},{225,16000},{270,120000},{315,64900}
        };
        const double measured = 10000.0 * adc / (4095u - adc);
        double best = 1e30, second = 1e30;
        unsigned angle = 0;
        for (const auto& entry : table) {
            const double relative = fabs(measured - entry.ohms) / entry.ohms;
            if (relative < best) { second=best; best=relative; angle=entry.degrees; }
            else if (relative < second) second=relative;
        }
        if (best > 0.12 || second - best < 0.06) return false;
        degrees = angle;
        return true;
    }
};

struct DirectionAccumulator {
    double east=0, north=0;
    unsigned count=0;
    void add(unsigned degrees) {
        constexpr double pi=3.14159265358979323846;
        const double rad=degrees*pi/180.0;
        east+=sin(rad); north+=cos(rad); ++count;
    }
    bool mean(unsigned& degrees) const {
        if (count==0) return false;
        const double strength = sqrt(east*east+north*north)/count;
        if (strength < 0.2) return false;
        constexpr double pi=3.14159265358979323846;
        double deg=atan2(east,north)*180.0/pi;
        if (deg<0) deg+=360;
        degrees=unsigned(floor(deg+0.5))%360;
        return true;
    }
    void reset() { east=0;north=0;count=0; }
};

inline bool batteryMillivolts(unsigned adc, unsigned vref_mv, unsigned& value) {
    if (adc > 4095 || vref_mv < 2500 || vref_mv > 3600) return false;
    const uint64_t millivolts = (uint64_t(adc)*vref_mv*2+2047)/4095;
    if (millivolts < 2000 || millivolts > 3650) return false;
    value=unsigned(millivolts);
    return true;
}
} // namespace farm
