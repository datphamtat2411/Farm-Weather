#pragma once
#include <stdint.h>

namespace farm::sim {
// Interrupt producer / foreground consumer. The caller serializes all access
// to this object with EXTI or a short interrupt-masked critical section.
// Store actual contact edges in logical time buckets even during I/O.
enum class Contact : uint8_t { Rain, Wind, Confirm };
enum class CaptureError : uint8_t { None, Clock, Backlog, Counter };
struct Captured {
    unsigned rain=0, wind=0;
    bool confirm=false;
};
template<unsigned Slots=256> class ContactCapture {
    static_assert(Slots>=4);
    volatile uint8_t rain[Slots]={},wind[Slots]={},confirm[Slots]={};
    volatile uint32_t origin=0,consumed=0;
    volatile bool ready=false;
    volatile CaptureError error=CaptureError::None;
    volatile uint32_t late=0;
public:
    void begin(uint32_t now){origin=now;consumed=0;ready=true;}
    CaptureError failure()const{return error;}
    uint32_t lateEdges()const{return late;}
    void push(Contact type,uint32_t now,uint32_t tickMs){
        if(!ready||error!=CaptureError::None)return;
        const uint32_t elapsed=now-origin;
        if(elapsed>0x7fffffffu||!tickMs){error=CaptureError::Clock;return;}
        uint32_t second=elapsed/tickMs+1;
        // If the interrupt observes the just-consumed boundary (e.g. SysTick
        // is pending), preserve that edge in the next open window. Do not
        // reopen a finalized record or silently lose a contact.
        if(second<=consumed){
            if(second!=consumed){error=CaptureError::Clock;return;}
            second=consumed+1;++late;
        }
        if(second-consumed>Slots){error=CaptureError::Backlog;return;}
        const unsigned index=second%Slots;
        if(type==Contact::Confirm){confirm[index]=1;return;}
        volatile uint8_t& counter=type==Contact::Rain?rain[index]:wind[index];
        if(counter==255){error=CaptureError::Counter;return;}
        ++counter;
    }
    Captured pop(){
        const unsigned index=(consumed+1)%Slots;
        const Captured result{rain[index],wind[index],confirm[index]!=0};
        rain[index]=wind[index]=confirm[index]=0;
        ++consumed;
        return result;
    }
};
}
