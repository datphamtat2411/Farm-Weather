#pragma once
#include <Arduino.h>
// Wokwi Blue Pill alternate pin assignment; production GPIO in physical_pins.hpp.
namespace farm::sim {
constexpr uint32_t direction=PA0, battery=PA1, wind=PB0, rain=PB1;
constexpr uint32_t scl=PB6,sda=PB7,buzzer=PB8,relay=PB9;
constexpr uint32_t wind_fault=PB10,confirm_button=PB12;
constexpr uint32_t uart_rx=PA10,uart_tx=PA9;
constexpr uint32_t acceleration=25,tick_ms=40;
}
