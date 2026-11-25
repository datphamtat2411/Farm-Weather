#include "capture.hpp"
#include <cassert>
#include <iostream>
int main(){
    using namespace farm::sim;
    ContactCapture<8> c;c.begin(1000);
    c.push(Contact::Wind,1000,40);c.push(Contact::Rain,1039,40);
    c.push(Contact::Wind,1040,40);c.push(Contact::Confirm,1041,40);
    auto a=c.pop();assert(a.wind==1&&a.rain==1&&!a.confirm);
    // A short backward observation at the already closed tick must not lose a pulse.
    c.push(Contact::Wind,1039,40);
    a=c.pop();assert(a.wind==2&&a.rain==0&&a.confirm&&c.lateEdges()==1);
    // A short button tap is preserved while foreground is doing flash I/O.
    c.push(Contact::Confirm,1121,40);c.push(Contact::Confirm,1122,40);
    assert(!c.pop().confirm);assert(c.pop().confirm);assert(!c.pop().confirm);
    assert(c.failure()==CaptureError::None);
    ContactCapture<8> backlog;backlog.begin(0);
    backlog.push(Contact::Rain,319,40); // eighth open slot is valid
    assert(backlog.failure()==CaptureError::None);
    backlog.push(Contact::Wind,320,40);assert(backlog.failure()==CaptureError::Backlog);
    ContactCapture<8> old;old.begin(1000);old.pop();old.pop();
    old.push(Contact::Wind,1001,40);assert(old.failure()==CaptureError::Clock);
    ContactCapture<8> wrap;wrap.begin(UINT32_MAX-19);
    wrap.push(Contact::Rain,5,40);assert(wrap.pop().rain==1);
    ContactCapture<8> full;full.begin(0);
    for(unsigned i=0;i<256;++i)full.push(Contact::Wind,1,40);
    assert(full.failure()==CaptureError::Counter);
    std::cout<<"Interrupt capture, tick boundaries, short confirmation and bounded backlog PASS\n";
}
