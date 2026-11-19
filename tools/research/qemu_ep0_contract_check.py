"""Native qtest of strict EP0 ownership, status ZLP, NRDY and explicit boundaries."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-dir',type=Path,required=True)
    p.add_argument('--report-dir',type=Path,required=True)
    args=p.parse_args();out=args.report_dir.resolve()
    if not out.is_relative_to(ROOT/'artifacts'):raise ValueError('Output must stay under artifacts')
    out.mkdir(parents=True,exist_ok=False)
    binary=args.qemu_dir.resolve()/'build/qemu-system-arm'
    checks=[]
    with tempfile.TemporaryDirectory(prefix='strict-ep0-qtest-') as temp:
        scratch=Path(temp);qp=scratch/'qtest';hp=scratch/'host'
        command=[str(binary),'-M','sony-a6400,dwc3-gadget=on','-accel','qtest','-display','none',
                 '-qtest',f'unix:{qp},server=on,wait=off',
                 '-chardev',f'socket,id=host,path={hp},server=on,wait=off',
                 '-global','cxd90045-dwc3.chardev=host','-global','cxd90045-dwc3.strict-ep0=on']
        with (out/'qemu.log').open('wb') as log:
            proc=subprocess.Popen(command,stdout=log,stderr=log)
            try:
                def connect(path):
                    channel=socket.socket(socket.AF_UNIX);channel.settimeout(2)
                    for _ in range(100):
                        try:channel.connect(str(path));return channel
                        except (FileNotFoundError,ConnectionRefusedError):
                            if proc.poll() is not None:raise RuntimeError('QEMU exited')
                            time.sleep(.05)
                    raise TimeoutError(str(path))
                with connect(qp) as test,connect(hp) as host:
                    stream=test.makefile('rwb',buffering=0)
                    def qt(line):
                        stream.write((line+'\n').encode());return stream.readline().decode().strip()
                    def write(address,value):
                        assert qt(f'writel {address:#x} {value:#x}')=='OK'
                    def read(address):
                        reply=qt(f'readl {address:#x}');assert reply.startswith('OK ')
                        return int(reply[3:],16)
                    def check(name,condition):
                        if not condition:raise AssertionError(name)
                        checks.append(name)
                    def token(line,expected):
                        host.sendall((line+'\n').encode())
                        reply=b''
                        while b'\n' not in reply:reply+=host.recv(4096)
                        check(line[:60],reply.decode()==expected+'\n')
                    def arm(ep,kind,length,extra=0,address=0x13000):
                        trb=0x12000+ep*0x100
                        for i,value in enumerate([address,0,length,(kind<<4)|0x803|extra]):write(trb+4*i,value)
                        reg=0xf020c800+ep*16
                        write(reg+4,trb);write(reg+8,0);write(reg+12,0x406)
                        return trb
                    def ack_events():write(0xf020c40c,read(0xf020c40c))
                    write(0xf020c400,0x10000);write(0xf020c408,4096)
                    write(0xf020c720,3);write(0xf020c704,0x80000000)
                    for ep in (0,1):
                        reg=0xf020c800+16*ep
                        write(reg+4,0x500);write(reg+8,0);write(reg+12,0x401)
                    host.sendall(b'CONN\n')
                    setup=arm(0,2,8)
                    token('SETUP 8006000100000400','ACK 0 8')
                    check('SETUP releases only owned TRB',read(setup+12)&1==0)
                    ack_events();write(0x13000,0x44332211)
                    data=arm(1,5,4)
                    check('IN remains owned before host token',read(data+12)&1==1)
                    host.settimeout(.05)
                    try:unsolicited=host.recv(4096)
                    except socket.timeout:unsolicited=b''
                    check('no automatic IN reply',unsolicited==b'');host.settimeout(2)
                    token('OUT 0 -','ERROR control-direction-or-stage')
                    check('wrong direction preserves IN ownership',read(data+12)&1==1)
                    token('IN 1','IN 1 11223344')
                    check('IN token releases TRB',read(data+12)&1==0)
                    ack_events()
                    token('OUT 0 -','NAK 0')
                    check('status NRDY event encoded',read(0x10008)==0x20c0)
                    count=read(0xf020c40c)
                    token('OUT 0 -','NAK 0')
                    check('repeated NAK does not duplicate pending NRDY',read(0xf020c40c)==count)
                    ack_events();status=arm(0,4,64)
                    check('NAK token was not queued across arm',read(status+12)&1==1)
                    token('OUT 0 -','ACK 0 0')
                    check('status ZLP releases TRB',read(status+12)&1==0)
                    check('status OUT residual preserved',read(status+8)==64)
                    ack_events();setup=arm(0,2,8)
                    token('SETUP 8006zz0100000400','ERROR setup-encoding-or-length')
                    check('malformed SETUP preserves ownership',read(setup+12)&1==1)
                    token('SETUP '+'00'*1100,'ERROR host-line-too-long')
                    check('overflow line not partially executed',read(setup+12)&1==1)
                    token('SETUP 0005010000000000','ACK 0 8');ack_events()
                    status=arm(1,3,0)
                    check('status IN waits for token',read(status+12)&1==1)
                    token('IN 1','IN 1 ')
                    check('status IN ZLP releases TRB',read(status+12)&1==0)
                    ack_events();arm(0,2,8);token('SETUP 8006000100000400','ACK 0 8');ack_events()
                    data=arm(1,5,4,extra=4)
                    token('IN 1','ERROR unsupported-trb-length-flags-or-chain')
                    check('chain boundary preserves HWO',read(data+12)&1==1)
                    data=arm(1,5,2048)
                    token('IN 1','ERROR unsupported-trb-length-flags-or-chain')
                    check('large transfer not silently truncated',read(data+12)&1==1)
                    data=arm(1,8,4)
                    token('IN 1','ERROR trb-control-type')
                    check('unsupported LINK type preserves HWO',read(data+12)&1==1)
                    data=arm(1,5,4,address=0xf020c100)
                    token('IN 1','ERROR payload-dma-read')
                    check('MMIO payload not fabricated as RAM',read(data+12)&1==1)
                    stream.close()
            finally:
                proc.terminate();proc.wait(10)
    report={'status':'passed','classification':'constructed native qtest fixtures',
            'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'checks':checks}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(f'PASS: {len(checks)} native EP0 contract checks')

if __name__=='__main__':main()
