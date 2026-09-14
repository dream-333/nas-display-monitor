#pragma once
// TFT_eSPI is used only as a RAM sprite renderer. RM67162 drives the panel.
#include "../lib/amoled/pins_config.h"
#define ST7789_DRIVER
#define TFT_SCLK 47
#define TFT_MISO -1
#define TFT_RST 17
#define LOAD_GLCD
#define LOAD_FONT2
#define LOAD_FONT4
