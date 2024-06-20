import spidev
import time
import json
import serial
import logging
import sys
import importlib.util
from AWSIoTPythonSDK.MQTTLib import AWSIoTMQTTClient

# 로거 설정
logger = logging.getLogger("AWSIoTPythonSDK.core")
logger.setLevel(logging.DEBUG)
streamHandler = logging.StreamHandler()
logger.addHandler(streamHandler)

# SPI 설정
spi = spidev.SpiDev()
spi.open(0, 0)  # 버스 0, 디바이스 0
spi.max_speed_hz = 1350000

# MH-Z19B CO2 센서를 위한 시리얼 설정
ser = serial.Serial('/dev/serial0', 9600, timeout=1)

def read_channel(channel):
    adc = spi.xfer2([1, (8 + channel) << 4, 0])
    data = ((adc[1] & 3) << 8) + adc[2]
    return data

def convert_to_db(adc_value):
    reference_voltage = 3.3
    sensor_voltage = (adc_value / 1023.0) * reference_voltage
    # 단순 변환: 3.3V에서 100dB, 0V에서 0dB 가정
    db_value = (sensor_voltage / reference_voltage) * 100
    return db_value

def convert_to_lux(adc_value):
    max_lux = 1000  # 최대 lux 값 (정확성을 위해 데이터 시트 참조)
    return max_lux - ((adc_value / 1023.0) * max_lux)

def read_co2():
    ser.write(b'\xFF\x01\x86\x00\x00\x00\x00\x00\x79')
    time.sleep(0.1)
    result = ser.read(9)
    if len(result) == 9 and result[0] == 0xFF and result[1] == 0x86:
        co2 = result[2] * 256 + result[3]
        return co2
    else:
        return None

# AWS IoT Core 엔드포인트 외부 파일에서 읽기
try:
    with open('/home/pi/Desktop/endpoint.txt', 'r') as file:
        host = file.readline().strip()
    print(f"파일에서 읽은 엔드포인트: {host}")
except Exception as e:
    print(f"파일에서 엔드포인트를 읽지 못했습니다: {e}")
    sys.exit(1)

rootCAPath = "/home/pi/certs/Amazon-root-CA-1.pem"
certificatePath = "/home/pi/certs/certificate.pem.crt"
privateKeyPath = "/home/pi/certs/private.pem.key"
clientId = "danjam"  # 클라이언트 ID
topic = "sensor/data"  # 게시할 주제

# AWS IoT 클라이언트 초기화
myAWSIoTMQTTClient = AWSIoTMQTTClient(clientId)
myAWSIoTMQTTClient.configureEndpoint(host, 8883)
myAWSIoTMQTTClient.configureCredentials(rootCAPath, privateKeyPath, certificatePath)
myAWSIoTMQTTClient.configureOfflinePublishQueueing(-1)  # 무한 오프라인 큐잉
myAWSIoTMQTTClient.configureDrainingFrequency(2)  # 드레이닝: 2 Hz
myAWSIoTMQTTClient.configureConnectDisconnectTimeout(10)  # 10 초
myAWSIoTMQTTClient.configureMQTTOperationTimeout(5)  # 5 초

# AWS IoT에 연결
try:
    print("MQTT에 연결 시도 중")
    myAWSIoTMQTTClient.connect()
    print("MQTT에 성공적으로 연결됨")
except Exception as e:
    print(f"MQTT에 연결 실패: {e}")
    sys.exit(1)

def publish_to_aws_iot(topic, payload_json):
    myAWSIoTMQTTClient.publish(topic, payload_json, 1)  # QoS 1
    print(f"데이터 게시됨: {payload_json}")

# test.py 파일을 import하고 실행하는 함수
def run_test_script():
    test_script_path = '/home/pi/Desktop/project/camera/test.py'
    spec = importlib.util.spec_from_file_location("test", test_script_path)
    test = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(test)
    print("test.py 스크립트가 실행되었습니다")

try:
    # 온도, 습도 및 CO2 평균을 위한 변수
    temperature_sum = 0.0
    humidity_sum = 0.0
    co2_sum = 0
    count = 0
    start_time = time.time()

    # test.py 스크립트 실행
    run_test_script()

    while True:
        try:
            # 소리 및 조도 센서 데이터 읽기
            sound_value = read_channel(0)  # CH0에서 읽기
            light_value = read_channel(1)  # CH1에서 읽기

            # 원시 ADC 값을 lux 및 dB로 변환
            light_level = convert_to_lux(light_value)
            sound_level = convert_to_db(sound_value)

            # 센서 값 디버그 출력
            print(f"원시 소리 값: {sound_value}, 소리 수준: {sound_level} dB")
            print(f"원시 조도 값: {light_value}, 조도 수준: {light_level} lux")

            # 임계값 확인
            light_exceeds_70 = light_level > 70
            sound_exceeds_520 = sound_value > 520

            # 온도 및 습도 값 시뮬레이션 (실제 센서 판독 값으로 교체)
            temperature = 25.5  # 예시 값
            humidity = 60.0  # 예시 값

            # CO2 값 읽기
            co2 = read_co2()
            if co2 is not None:
                print(f"CO2 농도: {co2} ppm")
                co2_sum += co2
            else:
                print("CO2 센서에서 데이터를 읽지 못했습니다.")

            # 평균을 위한 온도, 습도 및 CO2 누적
            temperature_sum += temperature
            humidity_sum += humidity
            count += 1

            # 60초마다 평균을 계산하고 데이터 전송
            if time.time() - start_time >= 60:
                avg_temperature = temperature_sum / count
                avg_humidity = humidity_sum / count
                avg_co2 = co2_sum / count

                # dht22 센서를 위한 페이로드
                dht22_payload = {
                    "sensor_id": "dht22",
                    "temperature": avg_temperature,
                    "humidity": avg_humidity
                }
                dht22_payload_json = json.dumps(dht22_payload)

                # dht22를 위한 MQTT 메시지 게시
                publish_to_aws_iot(topic, dht22_payload_json)
                print(f"데이터 게시됨 (dht22): {dht22_payload_json}")

                # CO2 센서를 위한 페이로드
                co2_payload = {
                    "sensor_id": "co2sensor",
                    "co2": avg_co2
                }
                co2_payload_json = json.dumps(co2_payload)

                # CO2 센서를 위한 MQTT 메시지 게시
                publish_to_aws_iot(topic, co2_payload_json)
                print(f"데이터 게시됨 (CO2): {co2_payload_json}")

                # 평균 변수 초기화
                temperature_sum = 0.0
                humidity_sum = 0.0
                co2_sum = 0
                count = 0
                start_time = time.time()

            # 조도 센서를 위한 페이로드
            light_payload = {
                "sensor_id": "lightsense",
                "light": light_exceeds_70,
                "temperature": temperature,  # 예시 값
                "humidity": humidity,  # 예시 값
                "sound": sound_exceeds_520
            }
            light_payload_json = json.dumps(light_payload)

            # lightsense를 위한 MQTT 메시지 게시
            publish_to_aws_iot(topic, light_payload_json)
            print(f"데이터 게시됨 (lightsense): {light_payload_json}")

            # 소리 센서를 위한 페이로드
            sound_payload = {
                "sensor_id": "soundsense",
                "sound": sound_exceeds_520,
                "temperature": temperature,  # 예시 값
                "humidity": humidity,  # 예시 값
                "light": light_exceeds_70
            }
            sound_payload_json = json.dumps(sound_payload)

            # soundsense를 위한 MQTT 메시지 게시
            publish_to_aws_iot(topic, sound_payload_json)
            print(f"데이터 게시됨 (soundsense): {sound_payload_json}")

            # 다음 읽기 전 1초 대기
            time.sleep(1)

        except RuntimeError as error:
            # 읽기 오류 처리
            print(f"런타임 오류: {error.args[0]}")
            time.sleep(2)  # 다시 시도하기 전에 대기
            continue

        except Exception as e:
            # 기타 오류 처리
            print(f"오류 발생: {e}")
            time.sleep(2)  # 다시 시도하기 전에 대기

except KeyboardInterrupt:
    print("프로그램이 중단되었습니다")
finally:
    spi.close()
    ser.close()
    myAWSIoTMQTTClient.disconnect()
    print("MQTT 연결이 종료되었습니다")