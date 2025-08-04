# Rockchip simple Makefile
ifeq ($(COMPILE_FOR_BUILDROOT),)
ifeq ($(MEDIA_PARAM), )
    MEDIA_PARAM:=../../Makefile.param
    include $(MEDIA_PARAM)
endif
endif

ifneq ($(V),)
export CMD_DBG=
else
export CMD_DBG=@
endif

SIMPLE_CC := $(RK_MEDIA_CROSS)-gcc
SIMPLE_AR := $(RK_MEDIA_CROSS)-ar

CURRENT_DIR := $(shell pwd)
SIMPLE_OUT_DIR :=$(CURRENT_DIR)/out

SIMPLE_PKG_CONF_OPTS += -DRKPLATFORM=ON

ifeq ($(RK_MEDIA_CHIP), rv1126)
SIMPLE_PKG_CONF_OPTS += -DCMAKE_SYSTEM_PROCESSOR=armv7l
SIMPLE_PKG_CONF_OPTS += -DARCH64=OFF
endif

SIMPLE_VO_LD_FLAGS := -lrockit

ifeq ($(RK_MEDIA_CHIP), rv1106)
SIMPLE_VO_LD_FLAGS := -lrockit_full
SIMPLE_PKG_CONF_OPTS += -DARCH64=OFF
SIMPLE_LD_FLAGS += -DRV1106_RV1103
SIMPLE_SPECIAL_SRC_DIR := rv1103_rv1106_rv1103b
endif

ifeq ($(RK_MEDIA_CHIP), rv1103b)
SIMPLE_PKG_CONF_OPTS += -DCMAKE_SYSTEM_PROCESSOR=armv7l
SIMPLE_PKG_CONF_OPTS += -DARCH64=OFF
SIMPLE_VO_LD_FLAGS := -lrockit_full
SIMPLE_LD_FLAGS += -DRV1103B
SIMPLE_SPECIAL_SRC_DIR := rv1103_rv1106_rv1103b
endif

SIMPLE_OPTS += -Wl,-rpath-link,${RK_MEDIA_OUTPUT}/lib

INC_FLAGS += -I$(RK_MEDIA_OUTPUT)/include
INC_FLAGS += -I$(RK_MEDIA_OUTPUT)/include/rkaiq
ifeq ($(RK_MEDIA_CHIP), rv1126)
INC_FLAGS += -I$(RK_MEDIA_OUTPUT)/include/rkaiq/uAPI
SIMPLE_LD_FLAGS += -DUAPI
SIMPLE_LD_FLAGS += -DRV1126_RV1109
else
INC_FLAGS += -I$(RK_MEDIA_OUTPUT)/include/rkaiq/uAPI2
SIMPLE_LD_FLAGS += -DUAPI2
endif

# INC_FLAGS += -I$(RK_MEDIA_OUTPUT)/include/rkaiq/common
# INC_FLAGS += -I$(RK_MEDIA_OUTPUT)/include/rkaiq/xcore
# INC_FLAGS += -I$(RK_MEDIA_OUTPUT)/include/rkaiq/algos
# INC_FLAGS += -I$(RK_MEDIA_OUTPUT)/include/rkaiq/iq_parser
# INC_FLAGS += -I$(RK_MEDIA_OUTPUT)/include/rkaiq/iq_parser_v2
# INC_FLAGS += -I$(RK_MEDIA_OUTPUT)/include/rkaiq/smartIr

SIMPLE_CFLAGS += -g -Wall $(INC_FLAGS) $(SIMPLE_PKG_CONF_OPTS) $(RK_MEDIA_CROSS_CFLAGS)

SIMPLE_LD_FLAGS += $(SIMPLE_OPTS) -L$(RK_MEDIA_OUTPUT)/lib -L$(RK_MEDIA_OUTPUT)/root/usr/lib \
					-lpthread -lm -lrockit \
					-lrockchip_mpp -DRKAIQ \
					-lrga
SIMPLE_LD_FLAGS += -Wl,--gc-sections -Wl,--as-needed

SIMPLE_VO_LD_FLAGS += $(RK_MEDIA_OPTS) -L$(RK_MEDIA_OUTPUT)/lib -L$(RK_MEDIA_OUTPUT)/root/usr/lib -Wl,-Bdynamic \
						-lrockchip_mpp -lrga -ldrm -lpthread

ifeq ($(RK_MEDIA_CHIP), rv1126)
SIMPLE_LD_FLAGS += -lasound
SIMPLE_VO_LD_FLAGS += -lasound
endif

SOURCES_C     = $(wildcard *.c)
ifneq ($(SIMPLE_SPECIAL_SRC_DIR),)
SPEICAL_SOURCES_C = $(wildcard $(strip $(SIMPLE_SPECIAL_SRC_DIR))/*.c)
endif

OBJECTS       = $(basename $(SOURCES_C)) $(basename $(SPEICAL_SOURCES_C))

$(warning "here add the debug info $(OBJECTS)")
all:
	$(CMD_DBG)test -f $(RK_MEDIA_OUTPUT)/lib/librockit.so  || make -C $(RK_MEDIA_TOP_DIR) media_libs
	$(CMD_DBG)make simple_build

simple_build: $(OBJECTS)
	$(CMD_DBG)mkdir -p $(SIMPLE_OUT_DIR)/bin
	$(CMD_DBG)mv -f $(OBJECTS) $(SIMPLE_OUT_DIR)/bin
	$(CMD_DBG)cp simple_test.sh simple_cmd.txt $(SIMPLE_OUT_DIR)/bin/
	$(call MAROC_COPY_PKG_TO_MEDIA_OUTPUT, $(RK_MEDIA_OUTPUT)/bin, $(SIMPLE_OUT_DIR)/bin)

ai_client_socket: ai_client_socket.c
	$(CMD_DBG)$(SIMPLE_CC) $^ -o $@ $(SIMPLE_CFLAGS) $(SIMPLE_LD_FLAGS)


clean:
	$(CMD_DBG)echo "clean simple"
	$(CMD_DBG)rm -rf $(SIMPLE_OUT_DIR)
