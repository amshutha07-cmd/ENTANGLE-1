// A.N.Sx Vault card reader (PN532 over I2C) — protocol v2
//
// The app talks to this sketch over USB serial at 115200 baud, one command per line:
//
//   VERSION                     -> ANSX_READER 2
//   UID:<ms>                    -> UID:<uid hex>            wait up to <ms> (max 60000) for a card
//                                  ERROR: No card            (never blocks forever, so the next command is heard)
//   READ:<uid>:<keyA>           -> DATA:<C|D>:<32 hex>       block 4 of THAT card; C = opened with the card's own
//                                                            key, D = still the factory key (FF FF FF FF FF FF)
//   WRITE:<uid>:<keyA>:<data>   -> SUCCESS: Written          writes block 4 of THAT card and sets its key A/B to <keyA>
//
// Errors are one line starting with "ERROR:". "Wrong card" means a different card is on the reader than the one
// named in the command: the app reads a card, checks it is not in use, and only then writes to the SAME card.
//
// Protocol v1 (READ / WRITE:<data>, factory key, no time limit) is still answered, so an older app keeps working.

#include <Wire.h>
#include <Adafruit_PN532.h>

#define PN532_IRQ   (2)
#define PN532_RESET (3)

Adafruit_PN532 nfc(PN532_IRQ, PN532_RESET);

const uint8_t FACTORY_KEY[6] = { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF };
const uint8_t DATA_BLOCK = 4;       // sector 1, block 0: the identity secret
const uint8_t TRAILER_BLOCK = 7;    // sector 1 trailer: key A | access bits | key B
const uint8_t ACCESS_BITS[4] = { 0xFF, 0x07, 0x80, 0x69 };   // factory access conditions: key A does everything
const uint16_t SELECT_MS = 1500;    // re-selecting a card that is already on the reader

char line[112];
uint8_t uid[7];
uint8_t uidLength;

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10);
  Serial.println(F("BOOTING..."));
  nfc.begin();
  if (!nfc.getFirmwareVersion()) {
    Serial.println(F("ERROR: Didn't find PN53x board"));
    while (1);
  }
  nfc.SAMConfig();
  Serial.println(F("ANSX_READER 2"));
  Serial.println(F("READY"));
}

// ── helpers ─────────────────────────────────────────────────────────────────────────────────────
int8_t hexValue(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return -1;
}

// Parse exactly `n` bytes of hex from `s`; returns the position after them, or NULL if malformed.
const char *parseHex(const char *s, uint8_t *out, uint8_t n) {
  for (uint8_t i = 0; i < n; i++) {
    int8_t hi = hexValue(s[2 * i]), lo = hexValue(s[2 * i + 1]);
    if (hi < 0 || lo < 0) return NULL;
    out[i] = (hi << 4) | lo;
  }
  return s + 2 * n;
}

void printHex(const uint8_t *b, uint8_t n) {
  for (uint8_t i = 0; i < n; i++) {
    if (b[i] < 0x10) Serial.print('0');
    Serial.print(b[i], HEX);
  }
}

bool selectCard(uint16_t ms) {
  return nfc.readPassiveTargetID(PN532_MIFARE_ISO14443A, uid, &uidLength, ms);
}

// Select the card named by `want` (4-byte UID) and authenticate its data sector, trying `key` first and then the
// factory key. Returns 'C' or 'D' for the key that worked, or prints an ERROR line and returns 0.
char openSector(const uint8_t *want, uint8_t *key) {
  if (!selectCard(SELECT_MS)) { Serial.println(F("ERROR: No card")); return 0; }
  if (uidLength != 4) { Serial.println(F("ERROR: Not Mifare Classic")); return 0; }
  if (memcmp(uid, want, 4) != 0) { Serial.println(F("ERROR: Wrong card")); return 0; }
  if (nfc.mifareclassic_AuthenticateBlock(uid, uidLength, DATA_BLOCK, 0, key)) return 'C';
  // A failed authentication drops the card out of the selected state: select it again before the next key.
  if (!selectCard(SELECT_MS) || memcmp(uid, want, 4) != 0) { Serial.println(F("ERROR: Card moved")); return 0; }
  if (nfc.mifareclassic_AuthenticateBlock(uid, uidLength, DATA_BLOCK, 0, (uint8_t *)FACTORY_KEY)) return 'D';
  Serial.println(F("ERROR: Auth Failed"));
  return 0;
}

// ── protocol v2 ─────────────────────────────────────────────────────────────────────────────────
void cmdUid(const char *arg) {
  long ms = atol(arg);
  if (ms <= 0 || ms > 60000) ms = 60000;
  if (selectCard((uint16_t)ms)) {
    Serial.print(F("UID:"));
    printHex(uid, uidLength);
    Serial.println();
  } else {
    Serial.println(F("ERROR: No card"));
  }
}

void cmdRead(const char *arg) {
  uint8_t want[4], key[6], data[16];
  const char *p = parseHex(arg, want, 4);
  if (!p || *p != ':' || !(p = parseHex(p + 1, key, 6)) || *p) { Serial.println(F("ERROR: Bad command")); return; }
  char which = openSector(want, key);
  if (!which) return;
  if (!nfc.mifareclassic_ReadDataBlock(DATA_BLOCK, data)) { Serial.println(F("ERROR: Block Read Failed")); return; }
  Serial.print(F("DATA:"));
  Serial.print(which);
  Serial.print(':');
  printHex(data, 16);
  Serial.println();
}

void cmdWrite(const char *arg) {
  uint8_t want[4], key[6], data[16], trailer[16];
  const char *p = parseHex(arg, want, 4);
  if (!p || *p != ':' || !(p = parseHex(p + 1, key, 6)) || *p != ':' || !(p = parseHex(p + 1, data, 16)) || *p) {
    Serial.println(F("ERROR: Bad command"));
    return;
  }
  if (!openSector(want, key)) return;
  if (!nfc.mifareclassic_WriteDataBlock(DATA_BLOCK, data)) { Serial.println(F("ERROR: Write Failed")); return; }
  // Lock the sector with the card's own key (A and B). Factory access bits, so only the keys change.
  memcpy(trailer, key, 6);
  memcpy(trailer + 6, ACCESS_BITS, 4);
  memcpy(trailer + 10, key, 6);
  if (!nfc.mifareclassic_WriteDataBlock(TRAILER_BLOCK, trailer)) { Serial.println(F("ERROR: Key Write Failed")); return; }
  Serial.println(F("SUCCESS: Written"));
}

// ── protocol v1 (older apps): factory key, waits for a card indefinitely ────────────────────────
void legacyRead() {
  Serial.println(F("WAITING_FOR_CARD"));
  uint8_t data[16];
  if (!selectCard(0)) { Serial.println(F("ERROR: Tag Read Failed")); return; }
  if (uidLength != 4) { Serial.println(F("ERROR: Not Mifare Classic")); return; }
  if (!nfc.mifareclassic_AuthenticateBlock(uid, uidLength, DATA_BLOCK, 0, (uint8_t *)FACTORY_KEY)) {
    Serial.println(F("ERROR: Auth Failed"));
    return;
  }
  if (!nfc.mifareclassic_ReadDataBlock(DATA_BLOCK, data)) { Serial.println(F("ERROR: Block Read Failed")); return; }
  Serial.print(F("DATA:"));
  printHex(data, 16);
  Serial.println();
}

void legacyWrite(const char *hex) {
  Serial.println(F("WAITING_FOR_CARD"));
  uint8_t data[16];
  memset(data, 0, 16);
  if (!parseHex(hex, data, 16)) { Serial.println(F("ERROR: Bad command")); return; }
  if (!selectCard(0)) { Serial.println(F("ERROR: Tag Read Failed")); return; }
  if (uidLength != 4) { Serial.println(F("ERROR: Not Mifare Classic")); return; }
  if (!nfc.mifareclassic_AuthenticateBlock(uid, uidLength, DATA_BLOCK, 0, (uint8_t *)FACTORY_KEY)) {
    Serial.println(F("ERROR: Auth Failed"));
    return;
  }
  if (nfc.mifareclassic_WriteDataBlock(DATA_BLOCK, data)) Serial.println(F("SUCCESS: Written"));
  else Serial.println(F("ERROR: Write Failed"));
}

// ── main loop ───────────────────────────────────────────────────────────────────────────────────
void loop() {
  if (!Serial.available()) return;
  size_t n = Serial.readBytesUntil('\n', line, sizeof(line) - 1);
  line[n] = '\0';
  while (n && (line[n - 1] == '\r' || line[n - 1] == ' ')) line[--n] = '\0';

  if (strcmp(line, "VERSION") == 0) Serial.println(F("ANSX_READER 2"));
  else if (strncmp(line, "UID:", 4) == 0) cmdUid(line + 4);
  else if (strncmp(line, "READ:", 5) == 0) cmdRead(line + 5);
  else if (strcmp(line, "READ") == 0) legacyRead();
  else if (strncmp(line, "WRITE:", 6) == 0) {
    // v2 is WRITE:<8 hex>:<12 hex>:<32 hex>; v1 is WRITE:<32 hex>
    if (strlen(line + 6) == 8 + 1 + 12 + 1 + 32 && line[14] == ':') cmdWrite(line + 6);
    else legacyWrite(line + 6);
  }
  else if (n) Serial.println(F("ERROR: Unknown command"));
}
