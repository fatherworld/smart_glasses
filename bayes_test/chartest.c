#include <stdio.h>
#include <errno.h>
#include <string.h>
#include <stdlib.h>
int main(){
    unsigned char data[100];
    strcpy(data,"开始录音");
    if(strcmp(data, "开始录音") == 0)
    {
        printf("开始了 \n");
    }
    else{
        printf("不匹配\n");
    }
    return 0;
}