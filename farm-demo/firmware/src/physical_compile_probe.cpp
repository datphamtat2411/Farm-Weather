#include <Arduino.h>
#include "physical_pins.hpp"
#include "weather.hpp"

#if !defined(FARM_PHYSICAL_COMPILE_PROBE) || !defined(STM32L476xx)
#error This non-deployable compile probe must target STM32L476RG.
#endif
// Compile/link only. No synthetic sensor values are presented as physical data.
// No GPIO, actuator, serial, RF or flash access. Real drivers belong to Tasks 02+.
farm::Station probe;
volatile unsigned compile_probe_status;
void setup() {
    probe.tick(0,0);
    compile_probe_status=probe.status;
}
void loop() {}
