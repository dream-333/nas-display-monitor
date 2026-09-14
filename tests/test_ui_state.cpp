#include "ui_state.h"
#include <cassert>
#include <limits>
int main(){
 Key k;
 assert(k.update(true,0)==KeyEvent::None);assert(k.update(true,2000)==KeyEvent::None);
 k.update(false,2010);k.update(true,2100);k.update(true,2130);
 k.update(false,2200);assert(k.update(false,2230)==KeyEvent::Short);
 k.update(true,2300);k.update(true,2330);assert(k.update(true,3230)==KeyEvent::Long);
 assert(k.update(true,5000)==KeyEvent::None);k.update(false,5010);assert(k.update(false,5040)==KeyEvent::None);
 Key bounce;bounce.update(false,0);bounce.update(true,10);bounce.update(false,20);assert(bounce.update(false,60)==KeyEvent::None);
 Key wrap;uint32_t t=UINT32_MAX-100;wrap.update(false,t);wrap.update(true,t+10);wrap.update(true,t+40);assert(wrap.update(true,t+940)==KeyEvent::Long);
 History h;assert(isnan(h.at(0,59)));
 float v[6]={1,2,3,4,5,6};h.push(v);assert(isnan(h.at(0,58)));assert(h.at(0,59)==1);
 for(int i=2;i<=65;i++){v[0]=i;h.push(v);}
 assert(h.at(0,0)==6);assert(h.at(0,59)==65);
 v[0]=NAN;h.push(v);assert(isnan(h.at(0,59)));assert(h.at(0,58)==65);
}
