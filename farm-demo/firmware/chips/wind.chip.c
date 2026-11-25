#include "wokwi-api.h"
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>

// Physical falling edges, not synthetic counts injected into firmware.
// Logical time runs 25x faster: 40 ms simulator = 1 logical second.
// A 400 us timer emits low pulses using a phase accumulator (0..750 Hz virtual).
typedef struct {pin_t out,fault_pin;uint32_t frequency,fault,phase;bool low;} state_t;
static void tick(void *data) {
  state_t *s=data;
  if(s->low){pin_write(s->out,1);s->low=false;}
  bool fault=attr_read(s->fault)!=0;
  pin_write(s->fault_pin,fault?1:0);
  uint32_t hz=fault?0:attr_read(s->frequency);
  if(hz>30) hz=30;
  if(!hz){s->phase=0;return;}
  s->phase+=hz*10;
  if(s->phase>=1000){
    s->phase-=1000;pin_write(s->out,0);s->low=true;
  }
}
void chip_init(void) {
  state_t *s=calloc(1,sizeof(*s));
  s->frequency=attr_init("frequency",6);
  s->out=pin_init("PULSE",OUTPUT_HIGH);
  s->fault=attr_init("fault",0);
  s->fault_pin=pin_init("FAULT",OUTPUT_LOW);
  timer_config_t cfg={.callback=tick,.user_data=s};
  timer_t timer=timer_init(&cfg);
  timer_start(timer,400,true);
}
