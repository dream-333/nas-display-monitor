#pragma once
#include <stdint.h>
#include <math.h>
enum class KeyEvent { None, Short, Long };
struct Key {
  bool raw=false, down=false, held=false, armed=false;
  uint32_t changed=0, began=0;
  KeyEvent update(bool pressed,uint32_t now) {
    // A key held at boot must first be released, especially GPIO0/BOOT.
    if(!armed){if(!pressed)armed=true;return KeyEvent::None;}
    if(pressed!=raw){raw=pressed;changed=now;}
    if(now-changed>=30 && down!=raw){
      down=raw;
      if(down){began=now;held=false;}
      else if(!held)return KeyEvent::Short;
    }
    if(down&&!held&&now-began>=900){held=true;return KeyEvent::Long;}
    return KeyEvent::None;
  }
};
struct History {
  static constexpr int Count=60, Channels=6;
  float values[Channels][Count];
  unsigned head=0, used=0;
  History(){for(auto &channel:values)for(float &v:channel)v=NAN;}
  void push(const float *v){for(int c=0;c<Channels;c++)values[c][head]=v[c];head=(head+1)%Count;if(used<Count)used++;}
  float at(int c,unsigned i)const{return i<Count-used?NAN:values[c][(head+Count-used+i-(Count-used))%Count];}
};
