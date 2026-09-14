#include <Arduino.h>
#include <algorithm>
#include "rm67162.h"
#include <TFT_eSPI.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <WebServer.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include <math.h>
#include <esp_system.h>
#include "protocol.h"
#include "usb_protocol.h"
#include "ui_state.h"
TFT_eSPI tft;
TFT_eSprite canvas(&tft);
WiFiUDP udp;
WebServer web(80);
Preferences prefs;
String ssid, password, token, apPassword;
bool setupMode=false, haveData=false, infoPage=false, usbData=false;
uint32_t receivedAt=0, redrawAt=0, reconnectAt=0, heartbeatAt=0;
const char *bootStage="starting";
void stage(const char *message) {
  bootStage=message;
  Serial.printf("[NAS-AMOLED-UI5.1-USB] %s\n",message);
  Serial0.printf("[NAS-AMOLED-UI5.1-USB] %s\n",message);
}
const uint16_t PORT=44445, BG=0x0000, FG=0xef7c, MUTED=0x9d34, ACCENT=0x6ef7;
const uint32_t STALE_MS=10000;
Metrics stats;
bool udpReady=false;
String value(float v,const char *unit="",int precision=1) { return isfinite(v)?String(v,precision)+unit:String("--"); }
String rate(float b) {
  if(!isfinite(b)) return "--";
  return b>=1048576?String(b/1048576,1)+" MiB/s":String(b/1024,1)+" KiB/s";
}
// All UI coordinates are native landscape pixels (536 x 240).
const uint16_t PANEL=0x10C3, TRACK=0x2126, CYAN=0x6ef7, PURPLE=0x6ef7, GREEN=0x6ef7;
#include "ui_font.h"
void textAt(int x,int y,const String &text,uint16_t color=FG,int font=2,int size=1,int datum=TL_DATUM) {
  int px=font==4?(size>=2?42:24):font==1?14:16;
  uiText(x,y+px,text,px,color,datum==TR_DATUM?2:datum==TC_DATUM?1:0);
}
void header(const String &title,const String &status,uint16_t color) {
  canvas.fillRoundRect(12,10,4,18,2,CYAN);
  textAt(24,8,title,FG,4);
  textAt(518,12,status,color,2,1,TR_DATUM);
}
void settingRow(int y,const String &label,const String &content) {
  canvas.fillRoundRect(12,y,512,43,7,PANEL);
  textAt(24,y+13,label,MUTED);
  textAt(510,y+10,content,FG,4,1,TR_DATUM);
}
#include "dashboard.h"
void draw() {
  if(screenOff)return;
  canvas.fillSprite(BG);
  if(menuOpen) {drawMenu();}
  else if(infoPage) {
    header("NAS / CONNECTION","INFO",CYAN);
    settingRow(43,"CONNECTION",usbData?String("USB-C"):WiFi.localIP().toString());
    settingRow(93,usbData?"PAIRING":"UDP PORT",usbData?String("NOT REQUIRED"):String(PORT));
    settingRow(143,"STATUS",usbData?(freshData()?"USB LIVE":"USB STALE"):setupMode?"SETUP AP":WiFi.status()==WL_CONNECTED?"CONNECTED":"RECONNECTING");
    textAt(14,207,"A / B: dashboard",MUTED);
    textAt(522,207,"Hold A: settings",MUTED,2,1,TR_DATUM);
  } else if(setupMode&&!usbData) {
    header("NAS / CONNECT","SETUP",CYAN);
    textAt(14,39,"USB-C to NAS: no Wi-Fi setup needed",GREEN);
    settingRow(61,"NETWORK","NAS-Display-Setup");
    settingRow(111,"PASSWORD",apPassword);
    textAt(14,168,"Or join this Wi-Fi and open the address below",MUTED);
    textAt(14,193,"192.168.4.1",CYAN,4);
    textAt(521,220,"LOCAL SETUP",MUTED,1,1,TR_DATUM);
  } else {dashboard();}

  lcd_PushColors(0,0,536,240,(uint16_t*)canvas.getPointer());
}
bool validToken(const String &s) {
  if(s.length()!=32) return false;
  for(unsigned i=0;i<s.length();++i) if(!((s[i]>='0'&&s[i]<='9')||(s[i]>='a'&&s[i]<='f'))) return false;
  return true;
}
void startSetup() {
  setupMode=true; WiFi.mode(WIFI_AP);
  char pw[17]; snprintf(pw,sizeof(pw),"%08lx%08lx",(unsigned long)esp_random(),(unsigned long)esp_random());
  apPassword=pw;
  stage(WiFi.softAP("NAS-Display-Setup",apPassword.c_str()) ? "setup AP started (192.168.4.1)" : "ERROR: setup AP failed");
  web.on("/",HTTP_GET,[](){
    web.send(200,"text/html; charset=utf-8",R"HTML(<!doctype html><meta name="viewport" content="width=device-width"><title>NAS Display</title><style>body{font:18px sans-serif;max-width:440px;margin:30px auto;padding:16px}input,button{box-sizing:border-box;width:100%;padding:12px;margin:8px 0}</style><h2>NAS 状态屏设置</h2><form method="post" action="/save"><label>2.4 GHz Wi-Fi 名称<input name="ssid" maxlength="32" required></label><label>Wi-Fi 密码<input name="password" type="password" maxlength="63"></label><label>NAS 配对码（config.json 中的 token）<input name="token" pattern="[0-9a-f]{32}" minlength="32" maxlength="32" required></label><button>保存并重启</button></form>)HTML");
  });
  web.on("/save",HTTP_POST,[](){
    String s=web.arg("ssid"),p=web.arg("password"),t=web.arg("token");
    if(!s.length()||s.length()>32||(p.length()&&(p.length()<8||p.length()>63))||!validToken(t)) {
      web.send(400,"text/plain; charset=utf-8","请检查 Wi-Fi 名称、密码与32位配对码。"); return;
    }
    prefs.putString("ssid",s); prefs.putString("password",p); prefs.putString("token",t);
    web.send(200,"text/plain; charset=utf-8","已保存，即将重启。连接失败时长按 A 进入设置，再选择 RESET WI-FI 并确认。");
    delay(800); ESP.restart();
  });
  web.begin();
}
void receivePacket() {
  int size=udp.parsePacket(); if(!size) return;
  if(size>1024){while(udp.available())udp.read();return;}
  char buffer[1025]; int n=udp.read(buffer,1024); if(n<=0)return; buffer[n]=0;
  if(usbData&&freshData())return; // Recent USB data takes priority over LAN packets.
  Metrics next;
  if(!decodePacket(buffer,n,token.c_str(),next)) return;
  stats=next;haveData=true;usbData=false;receivedAt=millis();
}
void receiveUsb() {
  static UsbLineBuffer lines;
  unsigned budget=2048; // Bound work per loop, including malformed input.
  while(budget--&&Serial.available()) {
    if(!lines.feed((char)Serial.read()))continue;
    if(!strcmp(lines.buffer,"NAS_DISPLAY_HELLO")) {
      Serial.println("{\"kind\":\"nas-display-ready\",\"v\":1}");
      continue;
    }
    Metrics next;uint32_t seq;
    if(!decodeUsbPacket(lines.buffer,strlen(lines.buffer),next,seq))continue;
    stats=next;haveData=true;usbData=true;receivedAt=millis();
    Serial.printf("{\"kind\":\"nas-display-ack\",\"v\":1,\"seq\":%lu}\n",(unsigned long)seq);
  }
}
void setup() {
  Serial.setRxBufferSize(2048); // One maximum-length JSON frame plus USB burst margin.
  Serial.begin(115200);
  Serial0.begin(115200, SERIAL_8N1, 44, 43);
  delay(1000); // bounded USB enumeration delay; never wait indefinitely
  stage("boot; USB CDC and UART0 115200 8N1");
  Serial.printf("[NAS-AMOLED-UI5.1-USB] flash=%u psram=%u heap=%u reset=%d\n",ESP.getFlashChipSize(),ESP.getPsramSize(),ESP.getFreeHeap(),(int)esp_reset_reason());
  Serial0.printf("[NAS-AMOLED-UI5.1-USB] flash=%u psram=%u heap=%u reset=%d\n",ESP.getFlashChipSize(),ESP.getPsramSize(),ESP.getFreeHeap(),(int)esp_reset_reason());
  pinMode(PIN_LED,OUTPUT);digitalWrite(PIN_LED,HIGH);
  pinMode(PIN_BUTTON_1,INPUT_PULLUP);
  pinMode(PIN_BUTTON_2,INPUT_PULLUP);
  stage("initializing RM67162 QSPI AMOLED 536x240");
  rm67162_init();lcd_setRotation(1);
  canvas.setColorDepth(16);
  if(!canvas.createSprite(536,240)) {
    while(true){stage("ERROR: sprite allocation failed");delay(2000);}
  }
  canvas.setSwapBytes(true);
  stage("sprite allocated; loading settings");
  prefs.begin("nas-display",false);ssid=prefs.getString("ssid","");password=prefs.getString("password","");token=prefs.getString("token","");
  skin=prefs.getUChar("skin",0)%3;page=prefs.getUChar("page",0)%4;
  // UI4 colored layouts become the approved mint design; keep MONO preference.
  // Once migrated, all three UI5 palette choices persist normally.
  if(prefs.getUChar("ui_version",4)<5){skin=skin==1?1:0;prefs.putUChar("skin",skin);prefs.putUChar("ui_version",5);}
  brightness=prefs.getUChar("light",80);if(brightness<20||brightness>100||brightness%20)brightness=80;
  idleChoice=prefs.getUChar("idle",0)%4;screenPower(true);lastActivity=millis();
  if(!ssid.length()||!validToken(token))startSetup();
  else{WiFi.mode(WIFI_STA);WiFi.setAutoReconnect(true);WiFi.begin(ssid.c_str(),password.c_str());}
  draw();
  stage("setup complete; entering loop");
  Serial.println("{\"kind\":\"nas-display-ready\",\"v\":1}");
}
void loop() {
  uint32_t now=millis();
  receiveUsb();
  bool rawA=digitalRead(PIN_BUTTON_1)==LOW,rawB=digitalRead(PIN_BUTTON_2)==LOW;
  handleKey(0,keys[0].update(rawA,now));handleKey(1,keys[1].update(rawB,now));
  if(wakeGuard&&!rawA&&!rawB&&!keys[0].down&&!keys[1].down)wakeGuard=false;
  if(resetConfirm&&now-confirmAt>=5000)resetConfirm=false;
  if(uiDirty&&now-saveAt>=1200)persistUi();
  if((!setupMode||usbData)&&!menuOpen&&!screenOff&&idleSeconds[idleChoice]&&now-lastActivity>=idleSeconds[idleChoice]*1000)screenPower(false);
  if(now-historyAt>=1000){
    historyAt=now;bool f=freshData();
    float sample[]={f?stats.ct:NAN,f?stats.gt:NAN,f?stats.mem:NAN,f?stats.rx:NAN,f?stats.tx:NAN,f?stats.d1:NAN};
    history.push(sample);
  }
  if(setupMode)web.handleClient();
  else if(WiFi.status()==WL_CONNECTED) {
    if(!udpReady) udpReady=udp.begin(PORT)==1;
    if(udpReady) receivePacket();
  } else {
    if(udpReady){udp.stop();udpReady=false;if(!usbData)haveData=false;}
    if(now-reconnectAt>=15000){reconnectAt=now;WiFi.reconnect();}
  }
  if(now-redrawAt>=500){redrawAt=now;draw();}
  if(now-heartbeatAt>=2000) {
    heartbeatAt=now;
    Serial.printf("[NAS-AMOLED-UI5.1-USB] alive stage=%s mode=%s wifi=%d data=%d heap=%u\n",bootStage,setupMode?"AP":"STA",(int)WiFi.status(),haveData,ESP.getFreeHeap());
    Serial0.printf("[NAS-AMOLED-UI5.1-USB] alive stage=%s mode=%s wifi=%d data=%d heap=%u\n",bootStage,setupMode?"AP":"STA",(int)WiFi.status(),haveData,ESP.getFreeHeap());
  }
  delay(5);
}
