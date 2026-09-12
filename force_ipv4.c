#define _GNU_SOURCE
#include <stddef.h>
#include <dlfcn.h>
#include <netdb.h>
#include <sys/socket.h>

static int (*real_getaddrinfo)(const char *node, const char *service,
                               const struct addrinfo *hints,
                               struct addrinfo **res) = NULL;

int getaddrinfo(const char *node, const char *service,
                const struct addrinfo *hints,
                struct addrinfo **res) {
    if (!real_getaddrinfo) {
        real_getaddrinfo = (int (*)(const char *, const char *, const struct addrinfo *, struct addrinfo **))dlsym(RTLD_NEXT, "getaddrinfo");
    }

    struct addrinfo modified_hints;
    if (hints) {
        modified_hints = *hints;
        if (modified_hints.ai_family == AF_UNSPEC) {
            modified_hints.ai_family = AF_INET;
        }
        hints = &modified_hints;
    } else {
        struct addrinfo def_hints = {0};
        def_hints.ai_family = AF_INET;
        hints = &def_hints;
    }

    return real_getaddrinfo(node, service, hints, res);
}
