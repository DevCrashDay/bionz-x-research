/* Freestanding ARM Thumb constructor. It starts /bin/sh on /tmp/jiritsu/run.sh.
 * It does not write /setting or /etc/ld.so.preload. The dynamic loader runs
 * launch through .init_array when the object is opened.
 */
.syntax unified
.thumb
.arch armv7-a
.text
.global launch
.type launch, %function
launch:
    push {r4, r5, lr}
    movs r7, #2
    svc #0
    cmp r0, #0
    bne done
    movs r5, #3
1:
    movs r7, #6
    mov r0, r5
    svc #0
    adds r5, #1
    cmp r5, #256
    blo 1b
    sub sp, #32
    adr r0, shell
    str r0, [sp]
    adr r0, script
    str r0, [sp, #4]
    movs r0, #0
    str r0, [sp, #8]
    adr r0, environment
    str r0, [sp, #16]
    movs r0, #0
    str r0, [sp, #20]
    movs r7, #11
    adr r0, shell
    mov r1, sp
    add r2, sp, #16
    svc #0
    movs r7, #1
    movs r0, #127
    svc #0
done:
    pop {r4, r5, pc}
    .balign 4
shell:
    .asciz "/bin/sh"
script:
    .asciz "/tmp/jiritsu/run.sh"
environment:
    .asciz "PATH=/bin:/sbin:/usr/bin:/usr/sbin"
    .asciz "A6400_EPHEMERAL_RAM=1"
    .balign 4

.section .init_array, "aw"
    .word launch
