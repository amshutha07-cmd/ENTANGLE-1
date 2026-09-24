#include <Wire.h>
#include <Adafruit_PN532.h>

// I2C interface
#define PN532_IRQ   (2)
#define PN532_RESET (3)

Adafruit_PN532 nfc(PN532_IRQ, PN532_RESET);

// Default MIFARE Classic key
uint8_t keya[6] = { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF };
// We will use block 4 for storing our hardware anchor
uint8_t targetBlock = 4;

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10); 
  Serial.println("BOOTING...");

  nfc.begin();

  uint32_t versiondata = nfc.getFirmwareVersion();
  if (!versiondata) {
    Serial.println("ERROR: Didn't find PN53x board");
    while (1); // halt
  }

  // Configure board to read RFID tags
  nfc.SAMConfig();
  Serial.println("READY");
}

void loop() {
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim();
    
    if (command == "READ") {
      handleRead();
    } 
    else if (command.startsWith("WRITE:")) {
      String payload = command.substring(6);
      handleWrite(payload);
    }
  }
}

void handleRead() {
  Serial.println("WAITING_FOR_CARD");
  
  uint8_t success;
  uint8_t uid[] = { 0, 0, 0, 0, 0, 0, 0 };
  uint8_t uidLength;
  
  // Wait indefinitely until a card is present
  success = nfc.readPassiveTargetID(PN532_MIFARE_ISO14443A, uid, &uidLength);
  
  if (success) {
    if (uidLength == 4) {
      // Authenticate block 4
      success = nfc.mifareclassic_AuthenticateBlock(uid, uidLength, targetBlock, 0, keya);
      
      if (success) {
        uint8_t data[16];
        success = nfc.mifareclassic_ReadDataBlock(targetBlock, data);
        if (success) {
          Serial.print("DATA:");
          for (int i=0; i<16; i++) {
             // Print as hex string to avoid raw byte issues over serial
             if (data[i] < 0x10) Serial.print("0");
             Serial.print(data[i], HEX);
          }
          Serial.println();
        } else {
          Serial.println("ERROR: Block Read Failed");
        }
      } else {
        Serial.println("ERROR: Auth Failed");
      }
    } else {
      Serial.println("ERROR: Not Mifare Classic");
    }
  } else {
    Serial.println("ERROR: Tag Read Failed");
  }
}

void handleWrite(String payload) {
  Serial.println("WAITING_FOR_CARD");
  
  uint8_t success;
  uint8_t uid[] = { 0, 0, 0, 0, 0, 0, 0 };
  uint8_t uidLength;
  
  success = nfc.readPassiveTargetID(PN532_MIFARE_ISO14443A, uid, &uidLength);
  
  if (success) {
    if (uidLength == 4) {
      success = nfc.mifareclassic_AuthenticateBlock(uid, uidLength, targetBlock, 0, keya);
      
      if (success) {
        uint8_t data[16];
        memset(data, 0, 16);
        
        // Convert hex payload back to bytes (assuming 32 char hex string = 16 bytes)
        if (payload.length() >= 32) {
            for (int i=0; i<16; i++) {
                String byteString = payload.substring(i*2, i*2+2);
                data[i] = (uint8_t) strtol(byteString.c_str(), NULL, 16);
            }
        } else {
            // Just copy raw string if it's short
            payload.getBytes(data, 16);
        }

        success = nfc.mifareclassic_WriteDataBlock(targetBlock, data);
        if (success) {
          Serial.println("SUCCESS: Written");
        } else {
          Serial.println("ERROR: Write Failed");
        }
      } else {
        Serial.println("ERROR: Auth Failed");
      }
    } else {
      Serial.println("ERROR: Not Mifare Classic");
    }
  } else {
    Serial.println("ERROR: Tag Read Failed");
  }
}
