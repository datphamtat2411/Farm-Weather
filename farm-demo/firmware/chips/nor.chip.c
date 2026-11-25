#include "wokwi-api.h"
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

// Logical 64 KiB NOR flash, 2 KiB erase pages, erased bytes 0xff.
// Custom I2C bridge protocol: opcode, big-endian address, optional data.
// This is a simulation peripheral, not an I2C part from the hardware BOM.
typedef struct {
  uint8_t memory[65536], command_memory[4096];
  uint32_t address;
  uint8_t command, phase, clock_index;
  bool clock_started;
  uint32_t command_attr,last_control;
  pin_t ready;
  uint8_t mailbox_index,blank;
  const char* mailbox;
  uint64_t clock_anchor;
  uint32_t clock_snapshot;
} state_t;
// External test gateway sends input bytes, never an outcome or firmware state.
static const char* commands[]={"","LINK DOWN","ACK DROP","LINK UP","ACK AUTO","ACK MANUAL","DOWNLINK 21 0a10010101010101010101010101010101011090e291d8061894e991d80620014801","DOWNLINK 21 0a10010101010101010101010101010101011090e291d8061894e991d80620014802","DOWNLINK 21 0a10020202020202020202020202020202021090e291d8061894e991d80620024803","DOWNLINK 21 0a100303030303030303030303030303030310ace191d8061891e291d80620014804","DOWNLINK 21 0a10040404040404040404040404040404041090e291d8061894e991d80620032802300938064084074805"};
static void control_tick(void *data){
  state_t *s=data;uint32_t value=attr_read(s->command_attr);
  if(value==s->last_control)return;
  s->last_control=value;
  if(value>0&&value<sizeof(commands)/sizeof(commands[0])){
    s->mailbox=commands[value];s->mailbox_index=0;pin_write(s->ready,1);
  }
}
static bool connect_cb(void *data,uint32_t address,bool read) {
  (void)address;
  state_t *s=data;
  if(!read)s->phase=0;
  return true;
}
static uint8_t read_cb(void *data) {
  state_t *s=data;
  if(s->command==6)return s->blank;
  if(s->command==7){
    if(!s->mailbox)return 0;
    uint8_t c=(uint8_t)s->mailbox[s->mailbox_index++];
    if(!c){s->mailbox=NULL;pin_write(s->ready,0);}
    return c;
  }
  if(s->command==3){
    if(s->clock_index>=4)return 0xff;
    return (uint8_t)(s->clock_snapshot>>(8*s->clock_index++));
  }
  if(s->command==4)return s->address<sizeof(s->command_memory)?s->command_memory[s->address++]:0xff;
  return s->address<sizeof(s->memory)?s->memory[s->address++]:0xff;
}
static bool write_cb(void *data,uint8_t value) {
  state_t *s=data;
  if(s->phase==0){s->command=value;s->phase=1;return value<=7;}
  if(s->phase==1){s->address=(uint32_t)value<<8;s->phase=2;return true;}
  if(s->phase==2){
    s->address|=value;s->phase=3;
    if(s->command==6){
      if(s->address!=0)return false;
      s->blank=1;
      for(unsigned i=0;i<sizeof(s->memory);++i)if(s->memory[i]!=0xff){s->blank=0;break;}
    }
    if(s->command==7){if(s->address!=0)return false;s->mailbox_index=0;}
    if(s->command==3){
      if(s->address!=0)return false;
      uint64_t now=get_sim_nanos();
      if(!s->clock_started){s->clock_anchor=now;s->clock_started=true;}
      if(now<s->clock_anchor)return false;
      uint64_t seconds=(now-s->clock_anchor)/40000000ull;
      if(seconds>UINT32_MAX)return false;
      s->clock_snapshot=(uint32_t)seconds;s->clock_index=0;
    }
    if(s->command==2){
      if(s->address%2048)return false;
      memset(s->memory+s->address,0xff,2048);
    }
    return true;
  }
  if(s->command==5){
    if(s->address>=sizeof(s->command_memory)||(s->command_memory[s->address]&value)!=value)return false;
    s->command_memory[s->address++]&=value;return true;
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
  memset(s->command_memory,0xff,sizeof(s->command_memory));
  i2c_config_t cfg={.address=0x50,.scl=pin_init("SCL",INPUT_PULLUP),
    .sda=pin_init("SDA",INPUT_PULLUP),.connect=connect_cb,.read=read_cb,
    .write=write_cb,.user_data=s};
  i2c_init(&cfg);
  s->command_attr=attr_init("scenarioCommand",0);
  s->ready=pin_init("COMMAND_READY",OUTPUT_LOW);
  timer_config_t timer_cfg={.callback=control_tick,.user_data=s};
  timer_t timer=timer_init(&timer_cfg);timer_start(timer,1000,true);
}
