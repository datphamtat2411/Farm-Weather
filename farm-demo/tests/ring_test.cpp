#include "class_a.hpp"
#include <cassert>
#include <iostream>
#include <array>

using namespace farm;
struct Nor {
    std::array<uint8_t,storage::bytes> b;
    int budget=-1;
    Nor(){b.fill(255);}
    bool read(unsigned at,uint8_t* p,unsigned n){
        if(at+n>b.size())return false;
        memcpy(p,b.data()+at,n);return true;
    }
    bool program(unsigned at,const uint8_t* p,unsigned n){
        assert(!(at%8)&&!(n%8)&&at+n<=b.size());
        for(unsigned i=0;i<n;++i){
            if(budget==0)return false;
            if(budget>0)--budget;
            if((b[at+i]&p[i])!=p[i])return false;
            b[at+i]&=p[i];
        }
        return true;
    }
    bool erasePage(unsigned at){assert(!(at%storage::page));memset(b.data()+at,255,storage::page);return true;}
};
using Ring=storage::Ring<Nor>;
static storage::Frame make(Ring& r,uint8_t port=10){
    const auto seq=r.reserve();assert(seq);
    wire::Payload p;
    if(port==10){
        wire::Record a{};a.sequence=seq;a.ts=1795453200+seq;a.period_s=300;a.valid=quality::ALL;
        a.temperature=-125;a.humidity=700;a.pressure=10000;a.wind=100;a.gust=200;a.direction=135;a.rain=18;a.battery=3200;
        p=wire::encode(a);
    }else{
        wire::Event e{};e.sequence=seq;e.ts=1795453200+seq;e.requested_period=300;e.changed=1;e.status=1;e.mode=wire::Mode::Alarm;e.stop=true;e.buzzer=true;
        p=wire::encode(e);
    }
    assert(p.ok);return {seq,port,p};
}
static void same(const storage::Frame& a,const storage::Frame& b){
    assert(a.sequence==b.sequence&&a.port==b.port&&a.payload.size==b.payload.size&&!memcmp(a.payload.data,b.payload.data,a.payload.size));
}
int main(){
    uint32_t timestamp=0;
    assert(storage::timestamp(1795453200,0,300,timestamp)&&timestamp==1795453500);
    assert(storage::timestamp(1795453200,400,300,timestamp)&&timestamp==1795453900);
    assert(!storage::timestamp(UINT32_MAX,1,0,timestamp));
    Nor flash;Ring r(flash);assert(r.begin());
    const auto a=make(r);assert(r.append(a)==storage::StoreResult::Stored);
    const auto b=make(r,11);assert(r.append(b)==storage::StoreResult::Stored);
    assert(!r.acknowledge(a.sequence)); // not sent
    Ring boot(flash);assert(boot.begin()&&boot.highwater==2&&boot.count==2);
    storage::Frame f;assert(boot.select(64,true,false,f));same(f,b);
    assert(!boot.select(1,true,false,f)); // payload limit cannot delete records
    link::ClassA<Ring> l(boot);
    unsigned tx=0;uint32_t lastSeq=0;
    auto send=[&](const storage::Frame& frame,uint32_t counter){assert(counter==++tx);lastSeq=frame.sequence;};
    l.tick(0,send);assert(lastSeq==2);
    wire::Payload ack;ack.field(1,2);
    assert(l.downlink(0,20,ack)==0&&l.downlink(1,21,ack)==0);
    // Drop first ACK; no cumulative deletion. Recovery alternates periodic.
    l.tick(3,send);l.tick(7,send);assert(tx==1);
    l.tick(8,send);assert(tx==2&&lastSeq==1);
    assert(l.downlink(9,20,ack)==1&&boot.count==1); // ACK older sent record
    ack=wire::Payload{};ack.field(1,1);
    assert(l.downlink(10,20,ack)==1&&boot.count==0);
    assert(l.downlink(10,20,ack)==0);
    Ring restored(flash);assert(restored.begin()&&restored.count==0&&restored.highwater==2);
    auto c=make(restored);assert(c.sequence==3&&restored.append(c)==storage::StoreResult::Stored);
    // Torn body write, then reboot: reserved number cannot be reused.
    auto d=make(restored);flash.budget=15;
    assert(restored.append(d)==storage::StoreResult::IoError);
    flash.budget=-1;Ring after(flash);assert(after.begin()&&after.highwater==4&&after.count==1);
    auto e=make(after);assert(e.sequence==5&&after.append(e)==storage::StoreResult::Stored);
    assert(after.select(64,false,false,f));same(f,c);
    // CRC damage must be detected, not interpreted as a record.
    flash.b[storage::metadata+2*storage::slot+10]^=1;
    Ring damaged(flash);assert(damaged.begin()&&damaged.corrupt>=1);
    // Exact capacity, event reserve, rollover and page-level reclamation.
    Nor fullFlash;Ring full(fullFlash);assert(full.begin());
    for(unsigned i=0;i<storage::slots-storage::event_reserve;++i){
        auto x=make(full);assert(full.append(x)==storage::StoreResult::Stored);
    }
    assert(full.nearFull());
    auto rejected=make(full);assert(full.append(rejected)==storage::StoreResult::Full);
    for(unsigned i=0;i<storage::event_reserve;++i){
        auto x=make(full,11);assert(full.append(x)==storage::StoreResult::Stored);
    }
    assert(full.count==960);auto extra=make(full,11);assert(full.append(extra)==storage::StoreResult::Full);
    Ring fullBoot(fullFlash);assert(fullBoot.begin()&&fullBoot.count==960&&fullBoot.highwater==962&&fullBoot.usedSlots==960);
    for(uint32_t seq=1;seq<=320;++seq){fullBoot.markSent(seq);assert(fullBoot.acknowledge(seq));}
    assert(!fullBoot.nearFull());
    for(unsigned i=0;i<64;++i){auto x=make(fullBoot);assert(fullBoot.append(x)==storage::StoreResult::Stored);}
    Ring reclaimed(fullFlash);assert(reclaimed.begin()&&reclaimed.count==704&&reclaimed.highwater==1026);
    // With no recoverable journal but a valid slot, fail closed rather than
    // issue an already used sequence. A torn reservation never reuses a
    // committed identity, even when journal page rollover is interrupted.
    Nor missing;Ring m(missing);assert(m.begin());auto first=make(m);
    assert(m.append(first)==storage::StoreResult::Stored);
    assert(m.append(first)==storage::StoreResult::Invalid);
    missing.erasePage(0);missing.erasePage(storage::page);
    Ring unsafe(missing);assert(!unsafe.begin());
    Nor rollover;Ring before(rollover);assert(before.begin());
    for(unsigned i=0;i<256;++i)assert(before.reserve()==i+1);
    rollover.budget=3;assert(!before.reserve());
    rollover.budget=-1;Ring again(rollover);assert(again.begin()&&again.highwater==256);
    assert(again.reserve()==257);
    // Fragmented common region cannot consume the physically reserved event page.
    Nor fragment;Ring fr(fragment);assert(fr.begin());
    for(unsigned i=0;i<928;++i){auto x=make(fr);assert(fr.append(x)==storage::StoreResult::Stored);}
    for(uint32_t seq=1;seq<=928;seq+=32){fr.markSent(seq);assert(fr.acknowledge(seq));}
    assert(fr.count==899&&fr.usedSlots==928);
    auto blocked=make(fr);assert(fr.append(blocked)==storage::StoreResult::Full);
    auto urgent=make(fr,11);assert(fr.append(urgent)==storage::StoreResult::Stored);
    // Malformed and grouped ACKs.
    link::Ack decoded;wire::Payload group;group.field(1,1);group.field(1,7);
    assert(link::decodeAck(group,decoded)&&decoded.count==2&&decoded.ids[1]==7);
    group.field(1,7);assert(!link::decodeAck(group,decoded));
    wire::Payload bad;bad.byte(8);bad.byte(128);bad.byte(0);assert(!link::decodeAck(bad,decoded));
    std::cout<<"Task 07 PASS: 960 slots, journal recovery, CRC, event reserve, Class A RX1/RX2, ACK loss and retransmission\n";
}
