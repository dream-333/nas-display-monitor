#pragma once
#include <string.h>
#include "ui_font.h"
// Same page/theme slots and button behavior as UI4; palettes now share one layout.
const char *skinNames[]={"MINT","MONO","AMBER"};
const char *pageNames[]={"OVERVIEW","THERMAL","NETWORK","STORAGE"};
uint8_t skin=0,page=0,brightness=80,idleChoice=0,menuRow=0;
bool menuOpen=false,screenOff=false,resetConfirm=false,wakeGuard=false;
uint32_t confirmAt=0,lastActivity=0,historyAt=0,saveAt=0;
bool uiDirty=false;
Key keys[2]; History history;
const uint32_t idleSeconds[]={0,60,300,900};
bool freshData(){return haveData&&millis()-receivedAt<STALE_MS&&(usbData||WiFi.status()==WL_CONNECTED);}
const char *connectionStatus(){
  if(usbData)return freshData()?"USB LIVE":"USB STALE";
  if(setupMode)return "SETUP AP";
  if(WiFi.status()!=WL_CONNECTED)return "OFFLINE";
  return freshData()?"UDP LIVE":haveData?"UDP STALE":"WAIT DATA";
}
void touchSettings(){uiDirty=true;saveAt=millis();}
void persistUi(){prefs.putUChar("skin",skin);prefs.putUChar("page",page);prefs.putUChar("light",brightness);prefs.putUChar("idle",idleChoice);uiDirty=false;}
void screenPower(bool on){screenOff=!on;lcd_setBrightness(on?brightness*255/100:0);}

constexpr uint16_t INK=0xef7c,SOFT=0x9d34,RULE=0x2145;
uint16_t themeColor(int=0){const uint16_t colors[]={0x6ef7,0xef7c,0xf60e};return colors[skin%3];}
void uiTemperature(int x,int y,float v,int size,int maxWidth,uint16_t c) {
  String s=value(v,"",0);if(uiWidth(s,size)>maxWidth)size=size>42?42:30;
  uiText(x,y,s,size,INK);
  int unitX=x+uiWidth(s,size)+5;
  int unitSize=size>=78?30:20, r=size>=78?3:2;
  canvas.drawCircle(unitX+r,y-unitSize+5,r,c);
  // C is in the 24/20px alphabet, unlike the larger numeric-only font.
  uiText(unitX+2*r+3,y,"C",size>=78?30:20,c);
}
void uiDots(int center,int y) {
  for(int i=0;i<4;i++)canvas.fillRoundRect(center-26+i*15,y,i==page?11:4,3,1,i==page?themeColor():RULE);
}
void uiTop(const String &title,bool fresh) {
  uint16_t c=themeColor();
  canvas.fillRect(18,12,2,12,c);canvas.fillRect(23,14,2,8,c);canvas.fillRect(28,17,2,4,c);
  uiText(39,25,title,16,INK);
  String state=connectionStatus();
  uint16_t status=fresh?c:SOFT;
  int x=518-uiWidth(state,16);
  canvas.fillRoundRect(x-14,15,5,5,2,status);uiText(518,25,state,16,status,2);
}
void uiArrow(int x,int y,bool down,uint16_t c) {
  canvas.fillRect(x,y,2,18,c);
  int tip=down?y+17:y;
  for(int i=0;i<6;i++){int yy=down?tip-i:tip+i;canvas.fillRect(x-i,yy,2,2,c);canvas.fillRect(x+i,yy,2,2,c);}
}
void rateParts(float bytes,float &n,String &unit) {
  n=bytes/1024;unit="KiB/s";
  if(n>=1024){n/=1024;unit="MiB/s";}
  if(n>=1024){n/=1024;unit="GiB/s";}
}
void uiRate(int x,int baseline,float bytes,int size) {
  float n;String unit;rateParts(bytes,n,unit);
  String number=value(n,"",n>=100?0:1);
  uiText(x,baseline,number,size,INK);
  uiText(x+uiWidth(number,size)+7,baseline,unit,20,SOFT);
}
void uiChart(int x,int y,int w,int h,int channel,bool fresh) {
  if(!fresh)return;
  // Temperature: at least 20 Celsius of range, so noise never looks like a spike.
  // Traffic: zero-based, independent scale for download/upload.
  float lo=channel<2?150:0, hi=channel<2?-50:1;
  bool any=false;
  for(int i=0;i<History::Count;i++){float v=history.at(channel,i);if(isfinite(v)){any=true;lo=channel<2?std::min(lo,v):0;hi=std::max(hi,v);}}
  if(!any)return;
  if(channel<2){float mid=(lo+hi)/2;float span=std::max(20.f,hi-lo+8);lo=mid-span/2;hi=mid+span/2;}
  int px=0,py=0;bool previous=false;
  for(int i=0;i<History::Count;i++){
    float v=history.at(channel,i);if(!isfinite(v)){previous=false;continue;}
    int xx=x+i*(w-1)/(History::Count-1),yy=y+h-1-(int)(constrain((v-lo)/(hi-lo),0.f,1.f)*(h-1));
    if(previous){
      for(int fx=px;fx<=xx;fx++){
        int fy=py+(yy-py)*(fx-px)/std::max(1,xx-px);
        canvas.drawFastVLine(fx,fy,y+h-fy,uiBlend(themeColor(),2));
      }
      canvas.drawLine(px,py,xx,yy,themeColor());
    }
    px=xx;py=yy;previous=true;
  }
  if(previous)canvas.fillRoundRect(px-1,py-1,3,3,1,themeColor());
}
void uiFooter(const String &note) {
  canvas.drawFastHLine(18,198,500,RULE);uiText(18,226,note,20,SOFT);uiDots(491,218);
}
void dashboard() {
  bool fresh=freshData();uint16_t c=themeColor();
  uiTop(page==0?"DREAM NAS":pageNames[page],fresh);
  if(page==0){
    uiText(18,58,"CPU",20,SOFT);
    uiTemperature(18,132,fresh?stats.ct:NAN,78,146,c);
    uiText(288,83,"LOAD",16,SOFT,2);
    uiText(267,123,value(fresh?stats.cpu:NAN,"",0),30,INK,2);uiText(272,122,"%",20,SOFT);
    uiChart(18,153,270,38,0,fresh);
    canvas.drawFastVLine(308,44,143,RULE);
    uiText(328,58,"GPU",20,SOFT);
    uiTemperature(328,106,fresh?stats.gt:NAN,42,85,SOFT);
    uiText(500,104,value(fresh?stats.gpu:NAN,"",0),24,INK,2);uiText(504,103,"%",20,SOFT);
    canvas.drawFastHLine(328,124,190,RULE);
    uiText(328,151,"RAM",20,SOFT);
    uiText(494,182,value(fresh?stats.mem:NAN,"",0),42,INK,2);uiText(500,181,"%",20,SOFT);
    canvas.fillRoundRect(328,170,84,4,2,RULE);
    if(fresh&&isfinite(stats.mem)){int w=(int)(84*constrain(stats.mem,0.f,100.f)/100);if(w>0)canvas.fillRect(328,170,w,4,c);}
    canvas.drawFastHLine(18,198,500,RULE);
    uiArrow(24,209,true,c);uiRate(42,228,fresh?stats.rx:NAN,30);
    uiArrow(334,209,false,c);uiRate(352,228,fresh?stats.tx:NAN,30);
    uiDots(268,218);
  }else if(page==1){
    const char *labels[]={"CPU","GPU","SYS"};
    float temps[]={stats.ct,stats.gt,stats.st};
    for(int i=0;i<3;i++){
      int x=18+i*174;uiText(x,57,labels[i],20,SOFT);
      uiTemperature(x,103,fresh?temps[i]:NAN,42,93,c);
      if(i<2)canvas.drawFastVLine(x+155,44,65,RULE);
    }
    canvas.drawFastHLine(18,122,500,RULE);
    for(int i=0;i<2;i++){
      int x=18+i*272;
      uiText(x,148,i?"FAN 02":"FAN 01",20,SOFT);
      // Six-digit protocol limit still fits. Null means unavailable, never 0 RPM.
      String n=value(fresh?(i?stats.f2:stats.f1):NAN,"",0);
      int size=uiWidth(n,42)>176?30:42;
      uiText(x,186,n,size,INK);uiText(x+uiWidth(n,size)+8,185,"RPM",20,c);
    }
    uiFooter("DISK MAX");
    uiTemperature(140,228,fresh?stats.dh:NAN,24,65,SOFT);
  }else if(page==2){
    for(int i=0;i<2;i++){
      int x=18+i*272;uiArrow(x+6,47,i==0,c);
      uiText(x+25,61,i?"UPLOAD":"DOWNLOAD",20,SOFT);
      float n;String unit;rateParts(fresh?(i?stats.tx:stats.rx):NAN,n,unit);
      uiText(x,118,value(n,"",n>=100?0:1),42,INK);uiText(x,146,unit,20,c);
      uiChart(x,158,226,31,i?4:3,fresh);
    }
    canvas.drawFastVLine(268,44,143,RULE);uiFooter("LAST 60 SECONDS");
  }else{
    for(int i=0;i<4;i++){
      int x=18+(i%2)*272,y=57+(i/2)*82;
      if(i>=stats.diskCount){uiText(x,y,"NO DISK",20,SOFT);uiText(x,y+45,"--",42,INK);continue;}
      const DiskMetric &d=stats.disks[i];
      uiText(x,y,uiFit(d.name,20,226),20,SOFT);
      if(fresh&&strcmp(d.state,"ok")) {
        const char *state=!strcmp(d.state,"sleep")?"SLEEP":!strcmp(d.state,"old")?"STALE":!strcmp(d.state,"wait")?"WAIT":"N/A";
        uiText(x,y+39,state,24,SOFT);
      }else uiTemperature(x,y+45,fresh?d.temperature:NAN,42,150,c);
    }
    canvas.drawFastVLine(268,44,143,RULE);canvas.drawFastHLine(18,117,500,RULE);
    uiFooter(stats.diskTotal?String("DISKS ")+String((int)stats.diskStart+1)+"-"+String((int)stats.diskStart+stats.diskCount)+" / "+String((int)stats.diskTotal)+"    AUTO 10s":"NO DISK DATA");
  }
}
void drawMenu() {
  uiText(18,25,"DISPLAY SETTINGS",20,INK);uiText(518,24,"A NEXT / B SET",16,SOFT,2);
  const char *idleLabels[]={"NEVER","1 MIN","5 MIN","15 MIN"};
  String labels[]={"Brightness","Auto sleep","Connection info",resetConfirm?"Press B again to reset Wi-Fi":"Reset Wi-Fi","Back"};
  String values[]={String(brightness)+"%",String(idleLabels[idleChoice]),"","",""};
  for(int i=0;i<5;i++){
    int y=44+i*33;uint16_t bg=i==menuRow?0x10a2:0;
    if(i==menuRow){canvas.fillRoundRect(12,y-2,512,31,5,bg);canvas.fillRect(18,y+6,3,13,themeColor());}
    uiText(32,y+21,labels[i],20,i==menuRow?INK:SOFT,0,bg);
    uiText(505,y+21,values[i],20,themeColor(),2,bg);
  }
  uiText(18,231,"Hold A to return",16,SOFT);uiText(518,231,skinNames[skin],16,themeColor(),2);
}
void handleKey(int k,KeyEvent event){
  if(event==KeyEvent::None)return;
  lastActivity=millis();
  if(wakeGuard)return;
  if(screenOff){screenPower(true);wakeGuard=true;return;}
  if(event==KeyEvent::Long){
    resetConfirm=false;
    if(k==0){menuOpen=!menuOpen;infoPage=false;menuRow=0;}
    else if((!setupMode||usbData)&&!menuOpen)screenPower(false);
    return;
  }
  if(menuOpen){
    if(k==0){menuRow=(menuRow+1)%5;resetConfirm=false;return;}
    if(menuRow==0){brightness=brightness>=100?20:brightness+20;screenPower(true);touchSettings();}
    if(menuRow==1){idleChoice=(idleChoice+1)%4;touchSettings();}
    if(menuRow==2){menuOpen=false;infoPage=true;}
    if(menuRow==3){
      if(resetConfirm&&millis()-confirmAt<5000){prefs.remove("ssid");prefs.remove("password");prefs.remove("token");ESP.restart();}
      else{resetConfirm=true;confirmAt=millis();}
    }
    if(menuRow==4)menuOpen=false;
  }else if(infoPage){infoPage=false;}
  else if(!setupMode||usbData){if(k==0)page=(page+1)%4;else skin=(skin+1)%3;touchSettings();}
}
