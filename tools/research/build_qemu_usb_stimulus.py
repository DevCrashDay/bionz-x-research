"""Generate identity guards for a QEMU-only original USB-service stimulus."""
import argparse
import hashlib
import io
import json
from pathlib import Path
from elftools.elf.elffile import ELFFile

ROOT=Path(__file__).resolve().parents[2]
PARTS=ROOT/'firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev'
LIBS={
    'usb':('nflasha15_unpacked/lib/libUsbControl.so',0x04857000,'/usr/lib/libUsbControl.so','70cd5742fde9eea78c60905e4015531b4c3ab2668d2cb85c934531f451f1211f'),
    'mwf':('nflasha15_unpacked/lib/libMWF.so',0x040f7000,'/usr/lib/libMWF.so','a79dc0d558e6454e31e57ea3b026364be924d0e78c1b6101015125a249f7e274'),
    'libc':('nflasha7_unpacked/lib/libc-2.11.2.so',0x042c1000,'/lib/libc-2.11.2.so','bceb5cfb7769b54bf9c9d178b8a5f4579531c0f60e673cbcae872a8c7a3508fb'),
}
CAPTURED_HASHES={
    'usb':'b5cea9d2c92a0b0810353900871fa2806ca3064aa5d8cf3ca7db5ddb6f3940e2',
    'mwf':'0903f8f9257e71b1c6bfd76bc74863c686d5a6d13d2b982cccae63b4cd43a7b2',
    'libc':'2b294f4c256aebb2746cacddefc39c33c448a0d7f18c2813daa54109c445b910',
}
FUNCTIONS={
 'CONTROL_INSTANCE':('usb','_ZN10UsbControl11GetInstanceEv'),
 'OBJMSG_CTOR':('mwf','_ZN3MWF6ObjMsgC1Ejj'),
 'OBJMSG_DTOR':('mwf','_ZN3MWF6ObjMsgD1Ev'),
 'PARAM_CTOR':('mwf','_ZN3MWF17ParamStd_TemplateIiLNS_5Param4TypeE7EEC1Ei'),
 'PARAM_DTOR':('mwf','_ZN3MWF17ParamStd_TemplateIiLNS_5Param4TypeE7EED1Ev'),
 'ADD_PARAM':('mwf','_ZN3MWF6ObjMsg8AddParamEjRKNS_5ParamE'),
 'SEND_EVENT':('usb','_ZN3UFW9MsgSender7SendEvtEPN3MWF6ObjMsgE'),
 'MALLOC':('libc','malloc'), 'FREE':('libc','free'),
}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--captured-libs',type=Path,
                        help='explicit E0596 captured ELF profile; all three hashes remain pinned')
    args=parser.parse_args()
    out=args.output.resolve()
    if not out.is_relative_to(ROOT/'artifacts'): raise ValueError('Output must stay under artifacts')
    out.mkdir(parents=True,exist_ok=False)
    lines=[]; checks=[]; loaded={}; report={}
    for key,(rel,base,guest,expected) in LIBS.items():
        path=(args.captured_libs/Path(rel).name) if args.captured_libs else PARTS/rel
        if args.captured_libs:expected=CAPTURED_HASHES[key]
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected: raise ValueError('Input identity mismatch: '+rel)
        elf=ELFFile(io.BytesIO(raw)); loaded[key]=(raw,elf,base)
        h=2166136261
        for b in raw: h=((h^b)*16777619)&0xffffffff
        checks.append(f'(check_file("{guest}",0x{h:08x}u,{len(raw)}u) ? 1 : (log_value("file_{key}",0),0))')
        report[key]={'path':str(path.resolve()),'sha256':expected,'fnv':hex(h),'size':len(raw)}
    for label,(key,symbol) in FUNCTIONS.items():
        raw,elf,base=loaded[key]
        matches=elf.get_section_by_name('.dynsym').get_symbol_by_name(symbol)
        matches=[s for s in (matches or []) if s['st_shndx']!='SHN_UNDEF']
        if len(matches)!=1: raise ValueError('Missing or ambiguous definition '+symbol)
        va=matches[0]['st_value']; code=None
        for seg in elf.iter_segments():
            if seg['p_type']=='PT_LOAD' and seg['p_vaddr']<= (va&~1) < seg['p_vaddr']+seg['p_filesz']:
                off=seg['p_offset']+(va&~1)-seg['p_vaddr'];code=raw[off:off+16];break
        if code is None: raise ValueError('Function code absent')
        lines.append(f'#define F_{label} 0x{base+va:08x}u')
        lines.append(f'static const uint8_t usb_expected_{label}[16] __attribute__((used)) = {{'+','.join(hex(b) for b in code)+'};')
        checks.append(f'(same_code(F_{label},usb_expected_{label}) ? 1 : (log_value("code_{label}",F_{label}),0))')
        report[label]={'symbol':symbol,'elf_va':hex(va),'reference':hex(base+va),'bytes':code.hex()}
    lines.append('#define USB_INTERFACE_SLOT 0x04875ac4u')
    # Definition follows the local check helpers in the stimulus source.
    lines.append('#define verify_inputs() ('+' && '.join(checks)+')')
    (out/'usb_symbols.h').write_text('\n'.join(lines)+'\n')
    (out/'inputs.json').write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':main()
