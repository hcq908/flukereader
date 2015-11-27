#! /usr/bin/env python3

import argparse, serial, time, datetime, scipy, numpy, binascii

def processArguments():
    parser = argparse.ArgumentParser(description='Talk to a Fluke ScopeMeter.')

    parser.add_argument(
            '-p',
            '--port',
            required=True,
            help='serial port name (/dev/ttyS0)')

    parser.add_argument(
            '-i',
            '--identify',
            action='store_true',
            help='get identity of ScopeMeter')

    parser.add_argument(
            '-d',
            '--datetime',
            action='store_true',
            help='set the date/time of the ScopeMeter')

    parser.add_argument(
            '-s',
            '--screenshot',
            action='store_true',
            help='get a screenshot from the ScopeMeter')

    parser.add_argument(
            '-w', '--waveform',
            choices=[
                'A_TRACE',
                'A_ENVELOPE',
                'A_TREND',
                'B_TRACE',
                'B_ENVELOPE',
                'B_TREND'],
            action='append',
            help='capture waveform data')

    arguments = parser.parse_args()
    return arguments

def sendCommand(port, command, timeout=True):
    data = bytearray(command.encode("ascii"))
    data.append(ord('\r'))
    port.write(data)
    port.flush()
    ack = port.read(2)

    if len(ack) != 2:
        if timeout:
            print("error: command acknowledgement timed out")
            exit(1)
        else:
            return False
    
    if ack[1] != ord('\r'):
        print("error: did not receive CR after acknowledgement code")
        exit(1)

    code = int(chr(ack[0]))

    if code == 0:
        return
    elif code == 1:
        print("error: Command syntax error")
    elif code == 2:
        print("error: Command execution error")
    elif code == 3:
        print("error: Synchronization error")
    elif code == 4:
        print("error: Communication error")
    else:
        print(
                "error: Unknown error code ("
                +str(code)
                +") in command acknowledgement")
    exit(1)

    return True

def initializePort(portName):
    print("Opening and configuring serial port...", end="", flush=True)
    port = serial.Serial(portName, 1200, timeout=1)
    print("done")

    print("Reconciling serial port baud rate...", end="", flush=True)
    status = sendCommand(port, "PC 19200", False)
    port.baudrate = 19200
    if status == False:
        sendCommand(port, "PC 19200")
    print("done")

    return port

def identify(port):
    print("Getting identity of ScopeMeter...", end="", flush=True)
    sendCommand(port, "ID")
    identity = bytearray()
    while True:
        byte = port.read()
        if len(byte) != 1:
            print("error: timeout while receiving data")
            exit(1)
        if byte[0] == ord('\r'):
            break;
        identity.append(byte[0])

    identity = identity.split(b';')
    if len(identity) != 4:
        print("error: unable to decode identity string")
        exit(1)
    model = identity[0].decode()
    firmware = identity[1].decode()
    date = time.strptime(identity[2].decode(), "%Y-%m-%d")
    languages = identity[3].decode()
    print("done")
    print("     Model: "+model)
    print("   Version: "+firmware)
    print("Build Date: "+time.strftime("%B %d, %Y", date))

def dateTime(port):
    datetime = time.localtime(time.time()+1)
    print("Setting time of ScopeMeter...", end="", flush=True)
    sendCommand(port, "WT "+time.strftime("%H,%M,%S", datetime))
    print("done")
    print("Setting date of ScopeMeter...", end="", flush=True)
    sendCommand(port, "WD "+time.strftime("%Y,%m,%d", datetime))
    print("done")

def getUInt(data):
    return int.from_bytes(data, byteorder='big', signed=False)

def getInt(data):
    return int.from_bytes(data, byteorder='big', signed=True)

def getNumber(data, signed):
    if signed:
        return getInt(data)
    else:
        return getUInt(data)

def getFloat(data):
    mantissa = getInt(data[0:2])
    exponent = getInt(data[2:3])

    return float(mantissa * 10**exponent)

def getHeader(port, intSize):
    dataSize = 3+intSize
    data = port.read(dataSize)
    if len(data) != dataSize:
        print("error: header reception timed out")
        exit(1)
    if data[0:2] != b"#0":
        print("error: header preamble incorrect")
        exit(1)

    header = int(data[2])
    size = getUInt(data[3:3+intSize])

    return (header, size)

def getData(port, size):
    size += 1
    data = port.read(size)
    if len(data) != size:
        print("error: data reception timed out")
        exit(1)
    if not checksum(data[:-1], data[-1]):
        print("error: checksum failed")
        exit(1)
    return data[:-1]

def checksum(data, check):
    checksum = 0
    for byte in data:
        checksum += byte
        checksum %= 256

    return (checksum == check)

def screenshot(port):
    print("Downloading screenshot from ScopeMeter...", end="", flush=True)
    sendCommand(port, "QP 0,12,B")
    
    dataLength=""
    while True:
        byte = port.read()
        if len(byte) != 1:
            print("error: data length reception timed out")
            exit(1)
        if byte[0] == ord(','):
            dataLength = int(dataLength)
            break;
        dataLength += chr(byte[0])

    image = bytearray()
    status = 0
    retries = 0
    while True:
        # Let's initiate a segment transfer
        sendCommand(port, "{:d}".format(status))

        header, size = getHeader(port, 2)
        size += 2

        # Now let's fetch the data
        data = port.read(size)
        if len(data) != size:
            print("error: segment data reception timed out")
            exit(1)

        if not checksum(data[:-2], data[-2]):
            retries += 1
            if retries >= 3:
                print("error: segment checksum failed 3 times")
                exit(1)
            status = 1
            continue

        # Check for final CR
        if data[-1] != ord('\r'):
            print("error: did not receive terminating CR in segment")
            exit(1)

        retries = 0
        image += data[:-2]
        dataLength -= len(data)-2

        if dataLength == 0 or (header&0x80) != 0:
            if dataLength == 0 and (header&0x80) != 0:
                break
            else:
                print("error: mismatch in data received and header flag")
                exit(1)
    print("done")

    filename=time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime())+".png"
    print("Writing screenshot to "+filename+"...", end="", flush=True)
    imageFile = open(filename, 'wb')
    imageFile.write(image)
    imageFile.close()
    print("done")

class waveform_t:
    channel = ""
    trace_type = ""
    y_unit = ""
    x_unit = ""
    y_divisions = 0
    x_divisions = 0
    y_scale = 0.0
    x_scale = 0.0
    x_zero = 0.0
    y_at_0 = 0.0
    x_at_0 = 0.0
    delta_x = 0.0
    timestamp = None
    samples = None
    averaged = False

units = [
        None,
        "V",
        "A",
        "Ω",
        "W",
        "F",
        "K",
        "s",
        "h",
        "days",
        "Hz",
        "°",
        "°C",
        "°F",
        "%",
        "dBm 50 Ω",
        "dBm 600 Ω",
        "dBV",
        "dBA",
        "dBW",
        "VAR",
        "VA"]

def waveform(port, source):
    print("Downloading waveform admin data from ScopeMeter...", end="", flush=True)
    sendCommand(port, "QW "+source)

    # Handle the administrative data
    header, size = getHeader(port, 2)

    if size != 47:
        print("error: admin data is a weird size ({:d})".format(size))
        exit(1)

    #if header != 0:
    #    print("error: received admin data but no samples ({:d})".format(header))
    #    exit(1)

    data = getData(port, size)

    print("done")
    print("Processing waveform admin data from ScopeMeter...", end="", flush=True)

    waveform = waveform_t()

    waveform.y_unit = units[data[1]]
    waveform.x_unit = units[data[2]]
    waveform.y_divisions = getUInt(data[3:5])
    waveform.x_divisions = getUInt(data[5:7])
    waveform.y_scale = getFloat(data[7:10])
    waveform.x_scale = getFloat(data[10:13])
    y_zero = getFloat(data[15:18])
    waveform.x_zero = getFloat(data[18:21])
    y_resolution = getFloat(data[21:24])
    x_resolution = getFloat(data[24:27])
    waveform.y_at_0 = getFloat(data[27:30])
    waveform.x_at_0 = getFloat(data[30:33])
    waveform.timestamp = datetime.datetime(
            int(data[33:37].decode('ascii')),
            int(data[37:39].decode('ascii')),
            int(data[39:41].decode('ascii')),
            int(data[41:43].decode('ascii')),
            int(data[43:45].decode('ascii')),
            int(data[45:47].decode('ascii')))

    print("done")
    print("Downloading waveform sample data from ScopeMeter...", end="", flush=True)

    # Get our comma separator
    byte = port.read()
    if not (len(byte) == 1 and byte[0] == ord(',')):
        print("error: invalid separator between admin and samples")
        exit(1)

    # Handle the sample data
    header, size = getHeader(port, 4)
    #if header != 129:
    #    print("error: invalid header ({:d}) in sample data".format(header))
    #    exit(1)
    data = getData(port, size)

    print("done")
    print("Processing waveform sample data from ScopeMeter...", end="", flush=True)

    sample_signed = (data[0]&0b10000000 != 0)
    sample_size =    data[0]&0b00000111
    samples_per_sample = 1

    if data[0]&0b01110000 == 0b01000000:
        samples_per_sample = 2
    if data[0]&0b01110000 == 0b01100000:
        samples_per_sample = 3
    if data[0]&0b01110000 == 0b01110000:
        if "trend" in waveform.trace_type:
            samples_per_sample = 3
        else:
            samples_per_sample = 2

    pointer = 1
    overload = getNumber(data[pointer:pointer+sample_size], sample_signed)
    pointer += sample_size
    underload = getNumber(data[pointer:pointer+sample_size], sample_signed)
    pointer += sample_size
    invalid = getNumber(data[pointer:pointer+sample_size], sample_signed)
    pointer += sample_size
    nbr_of_samples = getUInt(data[pointer:pointer+2])
    pointer += 2

    waveform.samples = numpy.empty([nbr_of_samples, samples_per_sample])
    
    for i in range(nbr_of_samples):
        for j in range(samples_per_sample):
            sample = getNumber(
                    data[pointer:pointer+sample_size],
                    sample_signed)
            if sample == overload:
                waveform.samples[i][j] = numpy.inf
            elif sample == underload:
                waveform.samples[i][j] = -numpy.inf
            elif sample == invalid:
                waveform.samples[i][j] = numpy.nan
            else:
                waveform.samples[i][j] = y_zero + sample*y_resolution
            pointer += sample_size

    if pointer != size:
        print("error: number of samples does not match block size")
        exit(1)
    print("done")

    return waveform

def execute(arguments, port):
    if arguments.identify:
        identify(port)

    if arguments.datetime:
        dateTime(port)

    if arguments.screenshot:
        screenshot(port)

    if arguments.waveform != None:
        waveforms = []
        for i in arguments.waveform:
            i = i.split('_')
            channel = i[0]
            trace_type = i[1].title()
            source = ""
            
            if channel == 'A':
                source = "1"
            elif channel == 'B':
                source = "2"

            if trace_type == 'Trace':
                source += '0'
            elif trace_type == 'Envelope':
                source += '2'
            elif trace_type == 'Trend':
                source += '1'

            data = waveform(port, source)
            data.channel = channel
            if trace_type == "Trace" and data.samples.shape[1] == 2:
                matching = True
                for i in data.samples:
                    if i[0] != i[1]:
                        matching = False
                        break
                if matching:
                    data.samples = numpy.resize(
                            data.samples,
                            data.samples.shape[0])
                    data.trace_type = "Average"
                else:
                    data.trace_type = "Glitch"
            else:
                data.trace_type = trace_type
            waveforms += data

            filename=datetime.strftime("%Y-%m-%d-%H-%M-%S", data.timestamp) \
                    + "_input-" + data.channel \
                    + "_" + data.trace_type.lowercase() \
                    + "_" + data.x_unit + "-vs-" + data.y_unit \
                    + ".dat"
            datFile = open(filename, 'w')

arguments = processArguments()
port = initializePort(arguments.port)
execute(arguments, port)