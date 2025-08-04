/**
 * Copyright (C) 2012-2015 Yecheng Fu <cofyc.jackson at gmail dot com>
 * All rights reserved.
 *
 * Use of this source code is governed by a MIT-style license that can be found
 * in the LICENSE file.
 *
 *   module: argarse, developped by cofyc.jackson
 *  project: https://github.com/cofyc/argparse
 *
 */

#ifndef TEST_COMM_ARGPARSE_H
#define TEST_COMM_ARGPARSE_H

// 简化版本的参数解析头文件

#include <stdio.h>
#include <stdlib.h>

// 参数类型枚举
enum argparse_option_type {
    ARGPARSE_OPT_END,
    ARGPARSE_OPT_BOOLEAN,
    ARGPARSE_OPT_BIT,
    ARGPARSE_OPT_INTEGER,
    ARGPARSE_OPT_FLOAT,
    ARGPARSE_OPT_STRING,
    ARGPARSE_OPT_HELP,
};

// 参数选项结构体
struct argparse_option {
    enum argparse_option_type type;
    const char short_name;
    const char *long_name;
    void *value;
    const char *help;
    const char *metavar;
    int flags;
    int data;
};

// 参数解析器结构体
struct argparse {
    const struct argparse_option *options;
    const char * const *usages;
    int flags;
    const char *description;
    const char *epilog;
    int argc;
    const char **argv;
    const char **out;
    int cpidx;
    const char *optvalue;
};

// 宏定义
#define OPT_END()                   { ARGPARSE_OPT_END, 0, NULL, 0, 0, 0, 0, 0 }
#define OPT_HELP()                  { ARGPARSE_OPT_HELP, 'h', "help", NULL, "show this help message and exit", NULL, 0, 0 }
#define OPT_BOOLEAN(s, l, v, h, m, f, d)  { ARGPARSE_OPT_BOOLEAN, s, l, v, h, m, f, d }
#define OPT_INTEGER(s, l, v, h, m, f, d)  { ARGPARSE_OPT_INTEGER, s, l, v, h, m, f, d }
#define OPT_STRING(s, l, v, h, m, f, d)   { ARGPARSE_OPT_STRING, s, l, v, h, m, f, d }

// 函数声明
void argparse_init(struct argparse *self, struct argparse_option const *options,
                   const char * const *usages, int flags);

void argparse_describe(struct argparse *self, const char *description,
                       const char *epilog);

int argparse_parse(struct argparse *self, int argc, const char **argv);

#endif // TEST_COMM_ARGPARSE_H
