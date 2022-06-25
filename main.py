import re 
import os
import sys
import time
import serial

IPV4_PATTERN = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')
LTE_INTEFACE = 'eth1'
WAN_DEFAULT_GATEWAY = '192.168.1.1'

SERIAL_PATH = sys.argv[1]


def iproute2_set_network_address(address):
    # iptoute2 ip addr flush deletes default routes too
    system_commands = [
        'ip link set {} up'.format(LTE_INTEFACE),
        'ip route del default', 
        'ip addr flush {}'.format(LTE_INTEFACE),
        'ip addr add {}/24 broadcast + dev {}'.format(address, LTE_INTEFACE),
        'ip route add default via {}'.format(address),
        'ip link set arp off dev {}'.format(LTE_INTEFACE)
    ]

    for command in system_commands:
        os.system(command)

class ModemConnection:
    def __init__(self, transport):
        self.transport = transport
        self.commands = [
            'AT+CFUN=1',
            'AT+CMEE=2',
            'AT+COPS=0',
            'AT+XLEC=1',
            'AT+CGPIAF=1,0,0,0',
            'AT+XDNS=1,1',
            'AT+CGDCONT=1,"IP","internet.kpn.nl"',
            'AT+CGACT=1,1',
            'AT+CGPADDR=1',
            'AT+XDATACHANNEL=1,1,"/USBCDC/0","/USBHS/NCM/0",2,1',
            'AT+CGDATA="M-RAW_IP",1',
            'AT+CNMI=2,2',
            'AT+CMGF=1'
		]

    @property
    def sim_mode(self):
        # returns cfun mode
        pass

    def initialize(self):
        for command in self.commands:
            self.transport.send(command)
            self.recvieved_message(self.transport.receive(4))

        # Set modem operating state to True
        self.transport.operating = True
            
    def recvieved_message(self, msg):
        msg = msg.strip()


        if '+CGPADDR: 1,' in msg:
            ipv4_search = IPV4_PATTERN.search(msg)

            if ipv4_search is not None:
                ipv4_address = ipv4_search.group(0)
                if ipv4_address == '0.0.0.0':
                    return None

                iproute2_set_network_address(ipv4_address)

        if 'NO CARRIER' in msg:
            # We should enter flight mode to diconnect ourselfs on band level
            # And reconnect to the towsers and repeat the previous process from scratch
            
            print('CARRIER IS LOST NOT RESETTING')

        if 'activeren?' in msg:
            # We got an message saying we went though our data
            # We can send 10 messages to get a total of 20 gigs without opening their application on a phone
            
            print('Automatic action: SEND SMS for more data')

            for command in ['AT+CMGS="1280",129', 'NOG 1GB','\x1A']:
                self.transport.send(command)
                print(self.transport.receive())



class SerialModem:
    def __init__(self, path, baudrate, timeout=0.1):
        self.path = path
        self.baudrate = baudrate
        self.timeout = timeout
        self.operating = False
        self.transport = self.create_connection()

    def create_connection(self):
        device = serial.Serial(
            port = self.path,
            baudrate = self.baudrate,
            timeout = self.timeout
        )
        return device

    def close_connection(self):
        self.closed = True
        self.transport.close()
 
    def send(self, data):
        # Send if serial opened
        formated = '{}{}'.format(data, '\r\n')
        self.transport.write(formated.encode())

    def receive(self, timeout=2):
        buffer = ''
        lines_buffered = 0
        stime = time.perf_counter()
        
        # Reset serial console buffer
        self.transport.reset_output_buffer()
        self.transport.reset_input_buffer()

        while True:
            try:
                data = self.transport.readline().decode()

            except serial.serialutil.SerialException:
                self.close_connection()
                break

            if time.perf_counter() - stime>= timeout:
                return buffer 

            if data:
                lines_buffered+=1
                buffer+=data

            if lines_buffered >= 2:
                return buffer
            
            # Return read buffer instantly in operating mode
            if self.operating:
                return buffer
            
def main():
    modem = SerialModem(SERIAL_PATH, 115200, timeout=1)
    while True:
        
        connection = ModemConnection(modem)
        if not modem.operating:
            # The modem is not running or has been reset
            connection.initialize()

        else:
            # Start polling for more
            data = modem.receive()
            connection.recvieved_message(data)

main()
