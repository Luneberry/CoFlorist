#pragma once
#include <stddef.h>
/* Bounded application/x-www-form-urlencoded parser; rejects malformed escapes and NUL. */
static int cat_hex(unsigned char c) {
    if (c >= '0' && c <= '9') return c-'0';
    if (c >= 'a' && c <= 'f') return c-'a'+10;
    if (c >= 'A' && c <= 'F') return c-'A'+10;
    return -1;
}
static int cat_form_value(const char *body, size_t len, const char *key, char *out, size_t cap) {
    size_t key_len = strlen(key), found = 0;
    if (!out || !cap) return -1;
    out[0] = 0;
    for (size_t start = 0; start < len;) {
        size_t end = start;
        while (end < len && body[end] != '&') end++;
        if (end-start > key_len && !memcmp(body+start,key,key_len) && body[start+key_len]=='=') {
            if (found++) return -1;
            size_t n=0;
            for (size_t i=start+key_len+1;i<end;i++) {
                unsigned char c=(unsigned char)body[i];
                if (c=='+') c=' ';
                else if(c=='%') {
                    if(i+2>=end) return -1;
                    int a=cat_hex(body[i+1]),b=cat_hex(body[i+2]);
                    if(a<0||b<0) return -1;
                    c=(unsigned char)((a<<4)|b); i+=2;
                }
                if(!c||n+1>=cap) return -1;
                out[n++]=(char)c;
            }
            out[n]=0;
        }
        start=end+1;
    }
    return found ? 0 : -1;
}
