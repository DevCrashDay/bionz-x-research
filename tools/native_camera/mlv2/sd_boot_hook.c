/* Separate SD worker entry for the joint debugger/Mod Loader installation. */
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#define DEBUGGER "/setting/a6400_usb_debugd.elf"
#define WORKER "/setting/mlv2_sd_boot.sh"

__attribute__((constructor)) static void launch(void) {
    char exe[sizeof(DEBUGGER)];
    ssize_t size = readlink("/proc/self/exe", exe, sizeof(exe));
    if (size != sizeof(DEBUGGER) - 1 || memcmp(exe, DEBUGGER, sizeof(DEBUGGER) - 1) ||
        getuid() != 0 || access(WORKER, R_OK) != 0) return;

    pid_t child = fork();
    if (child < 0) return;
    if (child > 0) {
        while (waitpid(child, NULL, 0) < 0 && errno == EINTR) {}
        return;
    }
    if (fork() != 0) _exit(0);
    if (setsid() < 0) _exit(1);
    for (int fd = 0; fd < 256; fd++) close(fd);
    if (open("/dev/null", O_RDONLY) != 0 || open("/dev/null", O_WRONLY) != 1 ||
        dup2(1, 2) != 2) _exit(1);
    char *argv[] = {"sh", WORKER, NULL};
    char *env[] = {"PATH=/usr/bin:/bin:/sbin:/usr/sbin", NULL};
    execve("/bin/sh", argv, env);
    _exit(127);
}
