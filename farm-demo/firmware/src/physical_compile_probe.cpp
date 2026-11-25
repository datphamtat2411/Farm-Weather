#include <Arduino.h>
#include "physical_pins.hpp"
#include "weather.hpp"

#if !defined(FARM_PHYSICAL_COMPILE_PROBE) || !defined(STM32L476xx)
#error This non-deployable compile probe must target STM32L476RG.
#endif
// Compile/link only. No synthetic sensor values are presented as physical data.
// No GPIO, actuator, serial, RF or flash access. Real drivers belong to Tasks 02+.
farm::Station probe;
farm::AmbientProcessor pipeline;
farm::DirectionAccumulator direction;
volatile unsigned probe_direction;
volatile unsigned compile_probe_status;
void setup() {
    probe.tick(0,0);
    pipeline.ingest({25.0f,60.0f,1000.0f},true);
    direction.add(0);direction.add(45);
    unsigned angle=0;
    if(direction.mean(angle))probe_direction=angle;
    compile_probe_status=probe.status;
}
void loop() {}
