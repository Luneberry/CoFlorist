#include "cat_app.h"
#include "tal_network.h"
#include "http_host.h"
#include "portal_web_assets.h"
#include <string.h>
#include <stdio.h>
/* Read only our setup page over a real TCP socket. Do not print response bodies. */
static int cat_http_probe(void)
{
    int fd = tal_net_socket_create(PROTOCOL_TCP);
    if (fd < 0) { PR_ERR("Cat HTTP selftest socket failed"); return 0; }
    tal_net_set_timeout(fd, 2000, TRANS_RECV);
    tal_net_set_timeout(fd, 2000, TRANS_SEND);
    int result = tal_net_connect(fd, tal_net_str2addr("127.0.0.1"), 80);
    if (result) { PR_ERR("Cat HTTP selftest connect result=%d", result); tal_net_close(fd); return 0; }
    const char request[] = "GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n";
    unsigned sent=0;
    while (sent<sizeof(request)-1) {
        int n=tal_net_send(fd, request+sent, sizeof(request)-1-sent);
        if (n<=0) break;
        sent+=(unsigned)n;
    }
    char *response=tal_malloc(8192);
    if (!response) {tal_net_close(fd);return 0;}
    memset(response, 0, 8192);
    unsigned used=0;
    uint32_t start=tal_system_get_millisecond();
    while (used<8191 && (uint32_t)(tal_system_get_millisecond()-start)<6000) {
        int n=tal_net_recv(fd,response+used,8191-used);
        if(n<=0) break;
        used+=(unsigned)n;
    }
    response[used]=0;
    int header=http_host_find_header_end(response,used);
    int length=http_host_get_content_length(response);
    int status=0;sscanf(response,"HTTP/%*s %d",&status);
    const char *expected=portal_web_root_html();
    int match=header>0 && (size_t)(used-header)==strlen(expected) && !memcmp(response+header,expected,strlen(expected));
    PR_WARN("Cat HTTP selftest status=%d received=%u header=%d declared=%d body_match=%d",status,used,header,length,match);
    PR_WARN("Cat HTTP selftest prefix=%02x%02x%02x%02x",(unsigned char)response[0],(unsigned char)response[1],(unsigned char)response[2],(unsigned char)response[3]);
    tal_free(response);tal_net_close(fd);
    return status == 200 && match;
}

void cat_http_selftest(void)
{
    unsigned passed=0;
    /* Exceed the platform's eight TCP PCBs to catch close/reuse regressions. */
    for (unsigned i=0;i<12;i++) {
        if (!cat_http_probe()) break;
        passed++;
        tal_system_sleep(20);
    }
    PR_WARN("Cat HTTP repeat test passed=%u total=12",passed);
}
