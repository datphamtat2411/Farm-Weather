#include "wokwi-api.h"
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

// Register-level, deterministic BME280 model. Synthetic calibration is deliberately
// linear so user-controlled physical values can be encoded as raw sensor counts.
// Not a model of accuracy, noise, heating, current draw or conversion latency.
typedef struct {
  uint8_t regs[256], pointer;
  bool expect_pointer;
  uint32_t temp, humidity, pressure;
} chip_state_t;
static void u16(chip_state_t *s, uint8_t reg, uint16_t v) {
  s->regs[reg]=(uint8_t)v; s->regs[reg+1]=(uint8_t)(v>>8);
}
static void u20(chip_state_t *s,uint8_t reg,uint32_t v) {
  s->regs[reg]=(uint8_t)(v>>12); s->regs[reg+1]=(uint8_t)(v>>4);
  s->regs[reg+2]=(uint8_t)(v<<4);
}
static void sample(chip_state_t *s) {
  // Bosch compensation with T1=T2=16384, T3=0, P1=32768 and H2=128.
  // Traw = 262144 + T*5120; Praw = 2^20 - pressurePa*5.24288;
  // Hraw = RH*512. Rounded to closest representable raw count.
  const double t=attr_read_float(s->temp), p=attr_read_float(s->pressure), h=attr_read_float(s->humidity);
  u20(s,0xfa,(uint32_t)(262144.0+t*5120.0+0.5));
  u20(s,0xf7,(uint32_t)(1048576.0-p*524.288+0.5));
  uint16_t rh=(uint16_t)(h*512.0+0.5);
  s->regs[0xfd]=(uint8_t)(rh>>8); s->regs[0xfe]=(uint8_t)rh;
}
static void reset(chip_state_t *s) {
  memset(s->regs,0,sizeof(s->regs));
  s->regs[0xd0]=0x60;
  u16(s,0x88,16384); u16(s,0x8a,16384); u16(s,0x8e,32768);
  u16(s,0xe1,128);
  sample(s);
}
static bool connect_cb(void *data,uint32_t address,bool read) {
  (void)address;
  chip_state_t *s=data;
  s->expect_pointer=!read;
  return true;
}
static uint8_t read_cb(void *data) {
  chip_state_t *s=data;
  return s->regs[s->pointer++];
}
static bool write_cb(void *data,uint8_t value) {
  chip_state_t *s=data;
  if(s->expect_pointer){s->pointer=value;s->expect_pointer=false;return true;}
  uint8_t reg=s->pointer++;
  if(reg==0xe0 && value==0xb6) reset(s);
  else if(reg==0xf4) {
    s->regs[reg]=value;
    if(value&3) sample(s);
    if((value&3)==1 || (value&3)==2) s->regs[reg]&=~3;
  } else if(reg==0xf2 || reg==0xf5) s->regs[reg]=value;
  return true;
}
void chip_init(void) {
  chip_state_t *s=calloc(1,sizeof(*s));
  s->temp=attr_init_float("temperature",29.5f);
  s->humidity=attr_init_float("humidity",78.0f);
  s->pressure=attr_init_float("pressure",1008.4f);
  reset(s);
  i2c_config_t cfg={.address=0x76,.scl=pin_init("SCL",INPUT_PULLUP),
    .sda=pin_init("SDA",INPUT_PULLUP),.connect=connect_cb,.read=read_cb,
    .write=write_cb,.user_data=s};
  i2c_init(&cfg);
}
