#pragma once
#include <stdint.h>

// Report Chapter 3/4/6 design constants. Simulation time scaling belongs to its adapter.
namespace farm::design {
constexpr uint32_t ambient_period_s = 60;
constexpr uint32_t direction_period_s = 5;
constexpr uint32_t wind_window_s = 3;
constexpr uint32_t rain_window_s = 600;
constexpr uint32_t wind_debounce_ms = 5;
constexpr uint32_t rain_debounce_ms = 50;
constexpr uint32_t normal_period_s = 300;
constexpr uint32_t low_power_period_s = 900;
constexpr uint32_t wind_recovery_s = 60;
constexpr unsigned wind_alarm_pulses = 45; // 10 m/s in 3 s
constexpr unsigned wind_recovery_twice_pulses = 63; // strict <7 m/s
constexpr double rain_mm_per_tip = 0.2794;
constexpr double rain_on_mm = 5.0;
constexpr double rain_off_mm = 2.0;
constexpr double heat_on_c = 35.0;
constexpr double heat_off_c = 33.0;
constexpr unsigned battery_low_mv = 3000;
constexpr unsigned battery_recovery_mv = 3200;
constexpr unsigned confirmation_count = 3;
constexpr unsigned target_flash_queue_bytes = 64 * 1024;
constexpr unsigned queue_metadata_bytes = 4 * 1024;
constexpr unsigned queue_slot_bytes = 64;
static_assert((target_flash_queue_bytes - queue_metadata_bytes) / queue_slot_bytes == 960);
}
