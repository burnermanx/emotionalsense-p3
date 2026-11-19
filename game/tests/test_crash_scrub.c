#include <stdio.h>
#include <string.h>
#include "../crash_scrub.h"

static void put(char c, void *ctx) { char *o = ctx; size_t l = strlen(o); o[l] = c; o[l + 1] = 0; }
static int fails;
static void chk(const char *in, const char *home, const char *want) {
    char out[512] = "";
    es_scrub(in, strlen(in), home, put, out);
    if (strcmp(out, want)) { printf("FAIL: '%s' -> '%s' (want '%s')\n", in, out, want); fails++; }
}
int main(void) {
    chk("/home/alice/.local/share/x", "/home/alice", "~/.local/share/x");
    chk("/home/alice/.local/share/x", NULL, "/home/~/.local/share/x");
    chk("Z:/home/alice/game/data", "/home/bob", "Z:/home/~/game/data");
    chk("Z:\\home\\alice\\game", "", "Z:/home/~/game");
    chk("C:\\Users\\Alice Smith\\Desktop", "", "C:/Users/~/Desktop");
    chk("C:/Users/Alice/Desktop", "C:\\Users\\Alice", "C:/Users/~/Desktop");
    chk("c:/users/alice/x", "", "c:/users/~/x");
    chk("C:/USERS/alice", "", "C:/USERS/~");
    chk("/Users/alice/Library", "", "/Users/~/Library");
    chk("/Users/alice", "/Users/alice", "~");
    chk("D:\\Games\\ES\\data", "C:\\Users\\a", "D:/Games/ES/data");
    chk("/opt/homework/x /usr/share/Users.txt", "", "/opt/homework/x /usr/share/Users.txt");
    chk("home/alice", "", "home/~");
    chk("a\001" "b", "", "ab");
    if (!fails) puts("crash_scrub ok");
    return fails != 0;
}
