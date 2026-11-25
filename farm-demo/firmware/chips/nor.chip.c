#include "wokwi-api.h"
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

// Logical 64 KiB NOR flash, 2 KiB erase pages, erased bytes 0xff.
// Custom I2C bridge protocol: opcode, big-endian address, optional data.
// This is a simulation peripheral, not an I2C part from the hardware BOM.
typedef struct {
  uint8_t memory[65536];
  uint32_t address;
  uint8_t command, phase;
} state_t;
static bool connect_cb(void *data,uint32_t address,bool read) {
  (void)address;
  state_t *s=data;
  if(!read)s->phase=0;
  return true;
}
static uint8_t read_cb(void *data) {
  state_t *s=data;
  return s->address<sizeof(s->memory)?s->memory[s->address++]:0xff;
}
static bool write_cb(void *data,uint8_t value) {
  state_t *s=data;
  if(s->phase==0){s->command=value;s->phase=1;return value<=2;}
  if(s->phase==1){s->address=(uint32_t)value<<8;s->phase=2;return true;}
  if(s->phase==2){
    s->address|=value;s->phase=3;
    if(s->command==2){
      if(s->address%2048)return false;
      memset(s->memory+s->address,0xff,2048);
    }
    return true;
  }
  if(s->command!=1||s->address>=sizeof(s->memory))return false;
  // Forbid 0->1 transitions.
  if((s->memory[s->address]&value)!=value)return false;
  s->memory[s->address++]&=value;
  return true;
}
void chip_init(void) {
  state_t *s=calloc(1,sizeof(*s));
  memset(s->memory,0xff,sizeof(s->memory));
  i2c_config_t cfg={.address=0x50,.scl=pin_init("SCL",INPUT_PULLUP),
    .sda=pin_init("SDA",INPUT_PULLUP),.connect=connect_cb,.read=read_cb,
    .write=write_cb,.user_data=s};
  i2c_init(&cfg);
}
