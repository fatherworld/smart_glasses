// 简化版本的参数解析，避免编译依赖问题
#include "test_comm_argparse.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void argparse_init(struct argparse *self, struct argparse_option const *options,
                   const char * const *usages, int flags) {
    // 简化实现
    self->options = options;
    self->usages = usages;
    self->flags = flags;
}

void argparse_describe(struct argparse *self, const char *description,
                       const char *epilog) {
    // 简化实现
}

int argparse_parse(struct argparse *self, int argc, const char **argv) {
    // 简化实现 - 返回argc表示没有处理任何参数
    return argc;
}
