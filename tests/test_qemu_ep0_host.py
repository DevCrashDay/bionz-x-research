"""Host-side protocol validation without a guest, USB or physical media."""
import unittest
from unittest import mock
from tools.research.qemu_ep0_host import Ep0Host

DEVICE='12010002000000404c056f09000101020301'
CONFIG='0902220001010080fa09040000010300000009211101000122130007058103200001'

class Channel:
    def __init__(self,replies):self.replies=[r.encode()+b'\n' for r in replies];self.sent=[]
    def sendall(self,data):self.sent.append(data.decode().strip())
    def recv(self,size):return self.replies.pop(0) if self.replies else b''

def enumeration_replies():
    return ['ACK 0 8','IN 1 '+DEVICE,'ACK 0 0','ACK 0 8','IN 1 ',
            'ACK 0 8','IN 1 '+CONFIG[:18],'ACK 0 0',
            'ACK 0 8','IN 1 '+CONFIG,'ACK 0 0','ACK 0 8','IN 1 ',
            'ACK 0 8','IN 1 01','ACK 0 0']

class Ep0HostTests(unittest.TestCase):
    def test_all_control_phases_are_required(self):
        channel=Channel(enumeration_replies())
        result=Ep0Host(channel).enumerate_bus_power()
        self.assertTrue(result['control_sequence_completed'])
        self.assertFalse(result['uvc_streaming_verified'])
        self.assertEqual(channel.sent.count('OUT 0 -'),4)
        self.assertIn('SETUP 0005010000000000',channel.sent)
        self.assertIn('SETUP 0009010000000000',channel.sent)
        self.assertEqual(len(channel.sent),16)
        self.assertEqual(channel.replies,[])

    @mock.patch('tools.research.qemu_ep0_host.time.sleep')
    def test_nak_retries_the_same_token(self,sleep):
        channel=Channel(['NAK 1','IN 1 '])
        self.assertEqual(Ep0Host(channel).token('IN 1','IN 1 '),'')
        self.assertEqual(channel.sent,['IN 1','IN 1'])

    def test_wrong_endpoint_nak_is_an_error(self):
        with self.assertRaises(ValueError):Ep0Host(Channel(['NAK 0'])).token('IN 1','IN 1 ')

    def test_superset_ack_is_not_success(self):
        with self.assertRaises(ValueError):Ep0Host(Channel(['ACK 0 80'])).control('0005010000000000')

    def test_empty_device_data_is_not_success(self):
        with self.assertRaises(ValueError):
            Ep0Host(Channel(['ACK 0 8','IN 1 '])).control('8006000100001200')

    def test_original_configuration_readback_must_agree(self):
        replies=enumeration_replies();replies[-2]='IN 1 02'
        with self.assertRaises(ValueError):Ep0Host(Channel(replies)).enumerate_bus_power()

    def test_stall_and_eof_are_boundaries(self):
        with self.assertRaises(RuntimeError):Ep0Host(Channel(['STALL 1'])).token('IN 1','IN 1 ')
        with self.assertRaises(EOFError):Ep0Host(Channel([])).token('IN 1','IN 1 ')

if __name__=='__main__':unittest.main()
