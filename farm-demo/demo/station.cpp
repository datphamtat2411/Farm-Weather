// Native presentation adapter: same firmware core, never a Wokwi hardware claim.
#include "weather.hpp"
#include "power.hpp"
#include "class_a.hpp"
#include <array>
#include <iostream>
#include <iomanip>
#include <stdexcept>
#include <string>
using namespace farm;
struct Nor {
    std::array<uint8_t,storage::bytes> bytes;
    Nor(){bytes.fill(255);}
    bool read(unsigned at,uint8_t* p,unsigned n){
        if(at>bytes.size()||n>bytes.size()-at)return false;
        memcpy(p,bytes.data()+at,n);return true;
    }
    bool program(unsigned at,const uint8_t* p,unsigned n){
        if(at%8||n%8||at>bytes.size()||n>bytes.size()-at)return false;
        for(unsigned i=0;i<n;++i)if((bytes[at+i]&p[i])!=p[i])return false;
        for(unsigned i=0;i<n;++i)bytes[at+i]&=p[i];
        return true;
    }
    bool erasePage(unsigned at){
        if(at%storage::page||at>bytes.size()-storage::page)return false;
        memset(bytes.data()+at,255,storage::page);return true;
    }
};
static void check(bool ok){if(!ok)throw std::runtime_error("native station invariant failed");}
static std::string hex(const wire::Payload& p){
    static const char digits[]="0123456789abcdef";
    std::string s;
    for(size_t i=0;i<p.size;++i){s+=digits[p.data[i]>>4];s+=digits[p.data[i]&15];}
    return s;
}
static wire::Payload unhex(const std::string& s){
    wire::Payload p;
    if(s=="-")return p;
    check(s.size()%2==0&&s.size()<=2*wire::capacity);
    for(size_t i=0;i<s.size();i+=2){
        const auto part=s.substr(i,2);
        check(part.find_first_not_of("0123456789abcdef")==std::string::npos);
        check(p.byte(uint8_t(std::stoul(part,nullptr,16))));
    }
    return p;
}
struct Pulse {
    uint32_t last=0;bool seen=false;
    unsigned count(uint32_t base,unsigned n,unsigned debounce,bool bounce){
        unsigned accepted=0;
        auto edge=[&](uint32_t t){
            if(acceptPulse(t,last,seen,debounce)){++accepted;last=t;seen=true;}
        };
        for(unsigned i=0;i<n;++i){
            auto t=base+i*1000/n;
            edge(t);if(bounce)edge(t+1);
        }
        return accepted;
    }
};
int main(){
    try{
        Nor nor;storage::Ring<Nor> ring(nor);check(ring.begin());
        link::ClassA<decltype(ring)> radio(ring);
        Station station;LocalActuation act;AmbientProcessor atmosphere;
        DirectionAccumulator directions;ReadHealth windHealth,directionHealth,batteryHealth;
        Pulse wind,rain;
        unsigned batteryMv=3200,prev=0,prevFault=0;
        bool batteryValid=true,directionValid=false,previousConfirm=false;
        uint32_t epoch=0;check(bool(std::cin>>epoch));check(epoch>=1577836800u);
        unsigned frequency,tips,adc,batteryAdc;
        bool noise,busOk,windOk,connected,confirm;
        double temperature,humidity,pressure;
        std::string ack;
        auto store=[&](uint8_t port,const wire::Payload& p,uint32_t seq){
            check(p.ok&&ring.append({seq,port,p})==storage::StoreResult::Stored);
            std::cout<<"RECORD "<<port+0<<" "<<hex(p)<<"\n";
        };
        auto ambient=[&](){
            atmosphere.ingest({float(temperature),float(humidity),float(pressure)},busOk);
            if(atmosphere.temperature.valid)station.temperature(temperature);
            else station.temperatureInvalid();
        };
        auto direction=[&](){
            unsigned samples[5]={adc,adc,noise?4095u:adc,adc,adc};
            unsigned angle=0;
            directionValid=DirectionLookup::decode(median5(samples),angle);
            directionHealth.sample(directionValid);
            if(directionValid)directions.add(angle);
        };
        while(std::cin>>frequency>>tips>>noise>>adc>>batteryAdc>>temperature>>humidity>>pressure>>busOk>>windOk>>connected>>confirm>>ack){
            check(frequency<=30&&tips<=10&&adc<=4095&&batteryAdc<=4095);
            if(station.seconds==0){ambient();direction();}
            const uint32_t base=station.seconds*1000;
            const bool due=station.tick(wind.count(base,frequency,design::wind_debounce_ms,noise),
                rain.count(base,tips,design::rain_debounce_ms,noise),windOk);
            const auto now=station.seconds;
            windHealth.sample(windOk);
            if(SampleSchedule::ambient(now))ambient();
            if(SampleSchedule::direction(now))direction();
            if(SampleSchedule::battery(now)){
                unsigned samples[5]={batteryAdc,batteryAdc,noise?4095u:batteryAdc,batteryAdc,batteryAdc};
                batteryValid=batteryMillivolts(median5(samples),3300,batteryMv);
                batteryHealth.sample(batteryValid);
                if(batteryValid)station.battery(batteryMv);else station.batteryInvalid();
            }
            const unsigned faults=(atmosphere.temperature.fault?quality::TEMPERATURE:0u)|
                (atmosphere.humidity.fault?quality::HUMIDITY:0u)|(atmosphere.pressure.fault?quality::PRESSURE:0u)|
                (windHealth.fault?quality::WIND:0u)|(directionHealth.fault?quality::DIRECTION:0u)|
                (batteryHealth.fault?quality::BATTERY:0u);
            station.sensorFault(faults!=0);
            const auto oldMode=act.mode;
            act.update(now,station.status,station.windReady,confirm&&!previousConfirm);
            previousConfirm=confirm;
            const unsigned changed=prev^station.status,changedFault=prevFault^faults;
            uint32_t timestamp=0;check(storage::timestamp(epoch,0,now,timestamp));
            if(changed||changedFault||oldMode!=act.mode){
                wire::Event e{};e.sequence=ring.reserve();e.ts=timestamp;e.changed=changed;e.status=station.status;
                e.requested_period=station.period;e.fault=faults;e.changed_fault=changedFault;
                e.mode=act.mode==LocalMode::Alarm?wire::Mode::Alarm:act.mode==LocalMode::AwaitInspection?wire::Mode::AwaitInspection:wire::Mode::Normal;
                e.buzzer=act.buzzer;e.stop=act.stopRequest;e.has_wind=station.windReady;
                e.wind=unsigned(wire::x10(windSpeed(station.currentWindPulses,3)));e.rain=station.rainWindow;
                e.has_temp=atmosphere.temperature.valid;if(e.has_temp)e.temperature=wire::x10(temperature);
                e.has_battery=batteryValid;e.battery=batteryMv;
                store(wire::EVENT_PORT,wire::encode(e),e.sequence);
            }
            prev=station.status;prevFault=faults;
            if(due){
                wire::Record r{};r.sequence=ring.reserve();r.ts=timestamp;r.period_s=station.periodElapsed;
                r.valid=atmosphere.mask()|quality::RAIN;r.status=station.status;
                if(station.windPeriodValid)r.valid|=quality::WIND;
                if(directionValid&&station.windPeriodValid&&station.windPeriod&&directions.mean(r.direction))r.valid|=quality::DIRECTION;
                if(batteryValid)r.valid|=quality::BATTERY;
                r.temperature=wire::x10(atmosphere.temperature.filtered);
                r.humidity=unsigned(wire::x10(atmosphere.humidity.filtered));
                r.pressure=unsigned(wire::x10(atmosphere.pressure.filtered));
                r.wind=unsigned(wire::x10(windSpeed(station.windPeriod,station.periodElapsed)));
                r.gust=unsigned(wire::x10(windSpeed(station.gust,3)));
                r.rain=station.rainPeriod;r.battery=batteryMv;
                store(wire::PERIODIC_PORT,wire::encode(r),r.sequence);
                station.finishPeriod();directions.reset();
            }
            if(connected&&ack!="-"){
                const unsigned accepted=radio.downlink(now,wire::ACK_PORT,unhex(ack));
                check(accepted==1);
                std::cout<<"ACK "<<accepted<<"\n";
            }
            radio.tick(now,[&](const storage::Frame& f,uint32_t counter){
                std::cout<<"TX "<<counter<<" "<<unsigned(f.port)<<" "<<hex(f.payload)<<" "<<connected<<"\n";
            });
            check(ring.ready());
            std::cout<<"STATE "<<now<<" "<<station.status<<" "<<station.period<<" "<<ring.count<<" "<<act.buzzer<<" "<<act.stopRequest<<"\n"<<std::flush;
        }
        check(std::cin.eof());
    }catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}
}
