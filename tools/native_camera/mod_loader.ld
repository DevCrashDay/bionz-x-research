SECTIONS
{
    . = 0x0412f3d0;
    .text : {
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    }
    /DISCARD/ : {
        *(.ARM.exidx*)
        *(.ARM.extab*)
        *(.comment*)
        *(.note*)
    }
}
