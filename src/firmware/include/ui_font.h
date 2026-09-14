#pragma once
#include "ui_font_data.h"
// Baseline coordinates, matching the approved 536 x 240 design.
// ESP32 const tables stay in flash. Runs of equal coverage reduce sprite writes.
const UiGlyph *uiGlyph(int size, unsigned char ch) {
  for(const auto &f:uiFonts) if(f.size==size) {
    for(int i=f.start;i<f.start+f.count;i++) if(uiGlyphs[i].code==ch)return &uiGlyphs[i];
    break;
  }
  return nullptr;
}
int uiWidth(const String &s,int size) {
  int w=0; for(unsigned i=0;i<s.length();i++){auto g=uiGlyph(size,s[i]);w+=g?g->advance:size/2;}
  return w;
}
uint16_t uiBlend(uint16_t c,int a,uint16_t bg=0) {
  int r=(((c>>11)&31)*a+((bg>>11)&31)*(15-a))/15;
  int g=(((c>>5)&63)*a+((bg>>5)&63)*(15-a))/15;
  int b=((c&31)*a+(bg&31)*(15-a))/15;
  return (r<<11)|(g<<5)|b;
}
void uiText(int x,int baseline,const String &s,int size,uint16_t c,int align=0,uint16_t bg=0) {
  int width=uiWidth(s,size);if(align==1)x-=width/2;else if(align==2)x-=width;
  for(unsigned i=0;i<s.length();i++) {
    auto g=uiGlyph(size,s[i]); if(!g){x+=size/2;continue;}
    for(int y=0;y<g->h;y++) {
      int start=0,previous=-1;
      for(int xx=0;xx<=g->w;xx++) {
        int index=y*g->w+xx;
        int a=xx==g->w?-1:(uiPixels[g->offset+index/2]>>(index%2?0:4))&15;
        if(a!=previous){if(previous>0)canvas.fillRect(x+g->x+start,baseline+g->y+y,xx-start,1,uiBlend(c,previous,bg));start=xx;previous=a;}
      }
    }
    x+=g->advance;
  }
}
String uiFit(String s,int size,int width) {
  if(uiWidth(s,size)<=width)return s;
  String result;
  for(unsigned i=0;i<s.length();i++){String next=result;next+=s[i];if(uiWidth(next+"..",size)>width)break;result=next;}
  return result+"..";
}
