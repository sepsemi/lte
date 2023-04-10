import os
import re
import time
import logging
import contextlib
from serial import Serial, SerialException

LTE_INTEFACE = 'eth1'
SERIAL_PORT = '/dev/ttyACM0'
DATAPLAN_APN = 'internet.kpn.de'

IPV4_PATTERN = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')

logging.basicConfig(
    format='[%(asctime)-8s][%(funcName)-8s()] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.DEBUG
)

def iproute2_set_network_address(address):
    # iptoute2 ip addr flush deletes default routes too
    system_commands = [
        'ip link set {} up'.format(LTE_INTEFACE),
        'ip route del default',
        'ip addr flush {}'.format(LTE_INTEFACE),
        'ip addr add {}/24 broadcast + dev {}'.format(address, LTE_INTEFACE),
        'ip route add default via {}'.format(address),
        'ip link set arp off dev {}'.format(LTE_INTEFACE),
    ]

    for command in system_commands:
        os.system(command)

def get_interface_status(interface):
    with open('/sys/class/net/{}/operstate'.format(interface)) as fp:
        return fp.read(32).rstrip()

class ModemConnection:
    COMMANDS = [
        'AT+CFUN=1',
        'AT+CMEE=2',
        'AT+CGPIAF=1,0,0,0',
        'AT+XDNS=1,1',
        'AT+CGDCONT=1,"IP",{}'.format(DATAPLAN_APN),
        'AT+COPS=0',
        'AT+CGACT=1,1',
        'AT+CGPADDR=1',
        'AT+XDATACHANNEL=1,1,"/USBCDC/0","/USBHS/NCM/0",2,1',
        'AT+CGDATA="M-RAW_IP",1',
        'AT+CNMI=2,2',
        'AT+XLEC=1',
        'AT+CMGF=1',
    ]

    BUFFER_SIZE = 1024
    # Timeout for initial connection, modem can hang
    INITIAL_READ_TIMEOUT = 2.0

    # received messages
    CELL_INFO = 'XLECI:'
    NO_CARRIER = 'NO CARRIER'
    SET_IP_ADDRESS = '+CGPADDR:'
    REQUEST_MORE_DATA = 'activeren?'

    def __init__(self, serial):
        self.connected = False
        self.serial = serial
        self.last_ack = time.perf_counter()

    def initialize(self):
        # initialize the modem for operation
        for command in self.COMMANDS:
            self.send_and_acknowledge(command)

    def set_ipv4(self, message):
        ipv4_search = IPV4_PATTERN.search(message)
        if ipv4_search is None:
            return None

        ipv4_address = ipv4_search.group(0)
        if ipv4_address == '0.0.0.0':
            return None
        
        logging.info('NAT address: ({})'.format(ipv4_address))
        iproute2_set_network_address(ipv4_address)

    def send_and_acknowledge(self, string, timeout=2):
        # try and send a message to the serial port
        self.serial.write('{}\r\n'.format(string).encode()) 

        # block until we get OK response
        while True:
            message = self.read()
            if 'OK' in message or len(message) < 8:
                break

            self.recvieved_message(message)

    def is_interface_up(self):
        status = get_interface_status(LTE_INTEFACE)
        return True if status == 'up' else False 
 
    def send(self, string):
        self.serial.write('{}\r\n'.format(string).encode()) 

    def read(self):
        try:
            data = self.serial.readline(self.BUFFER_SIZE)
            return data.decode().rstrip()
        except UnicodeDecodeError:
            # This is unhandled
            return ''

    def clear(self):
        # clear the buffers of the serial device
        self.serial.reset_output_buffer()
        self.serial.reset_input_buffer()

    def request_more_data(self):
        for string in ['AT+CMGS="1280",129', 'NOG 1GB','\x1A']:
            self.send(string)

    def recvieved_message(self, message):
        if len(message) > 1:
            self.last_ack = time.perf_counter()
            logging.debug('message: {}'.format(message))

        if self.SET_IP_ADDRESS in message:
            self.set_ipv4(message)
        
            if self.is_interface_up():
                self.connected = True

        elif self.NO_CARRIER in message:
            # The connection with the tower has been lost, we enter flightmode to reset the connection

            self.connected = False
            self.send_and_acknowledge('AT+CFUN=4')
            logging.warning('Carrier lost, entering flight mode')

        elif self.REQUEST_MORE_DATA in message:
            logging.info('Dataplan: Requesting more data')
            self.request_more_data()

        elif self.CELL_INFO in message:
            # Carrier agregation/cell info
            logging.debug('carrier cell: {}'.format(message[message.find('+XLECI:'):]))

    def connect_and_poll(self):
        # Bruteforce the initialization
        while not self.connected:
            # we can't wait forever, reset connection
            if self.last_ack + self.INITIAL_READ_TIMEOUT < time.perf_counter():
                return None

            self.initialize()
        
        # we are operational and connected
        logging.info('Connected')

        while self.connected is True:
            data = self.read()
            if data is None:
                break
            
            self.recvieved_message(data)

class Client:
    def __init__(self, port, timeout=0.1, rate=115200):
        self.port = port
        self.timeout = timeout
        self.rate = rate

    def create_serial(self):
        return Serial(
            dsrdtr=True,
            rtscts=True,
            port=self.port, 
            timeout=self.timeout, 
            baudrate=self.rate
        )

    def connect(self, reconnect):
        while reconnect is True:
            if not os.path.exists(self.port):
                time.sleep(self.timeout)
                continue

            # wait until the port is found
            self._serial = self.create_serial()
            self._connection = ModemConnection(self._serial)

            # reset the buffer
            self._connection.clear()

            # start polling for data
            try:
                self._connection.connect_and_poll()
            except SerialException:
                logging.warning('Forcefull detatch')
                self._serial.close()
                time.sleep(self.timeout)
                continue

            # the device not has been lost
            loging.warning('Connection lost: reconnecting')
            self._serial.close()

    def run(self, reconnect=True):
        self.connect(reconnect)

client = Client('/dev/ttyACM0')
client.run()
