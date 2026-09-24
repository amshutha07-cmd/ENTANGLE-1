// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title ANSXRegistry
 * @dev Public username -> (RSA public key, wallet, IP) directory for A.N.SXVault.
 *
 * Security properties:
 *  - A profile can only be registered by the wallet it names (ethAddr == msg.sender),
 *    so a username is bound to a real key-holder and SessionBroker offers can be
 *    attributed to an on-chain identity.
 *  - Only that wallet can later update the profile's IP.
 *  - First-come-first-served: a name cannot be re-registered. (Front-running of a
 *    desirable name is possible on any public chain; receivers therefore pin keys on
 *    first use and compare fingerprints out of band.)
 *  - Everything here is PUBLIC. Never store secrets. IP addresses are visible to all.
 */
contract ANSXRegistry {
    struct Profile {
        string  pubKey;
        string  ipAddr;
        address owner;
    }

    mapping(string => Profile) private profiles;
    string[] private userList;

    uint256 public constant MAX_NAME  = 32;
    uint256 public constant MAX_KEY   = 4096;
    uint256 public constant MAX_IP    = 64;

    event ProfileRegistered(string indexed username, string pubKey, string ipAddress, address ethAddr);
    event IPUpdated(string indexed username, string ipAddress);

    function registerProfile(
        string memory username,
        string memory pubKey,
        string memory ipAddr,
        address ethAddr
    ) public {
        require(ethAddr == msg.sender, "Profile must be registered by its own wallet.");
        uint256 nameLen = bytes(username).length;
        require(nameLen > 0 && nameLen <= MAX_NAME, "Bad username length.");
        require(bytes(pubKey).length > 0 && bytes(pubKey).length <= MAX_KEY, "Bad key length.");
        require(bytes(ipAddr).length <= MAX_IP, "Bad ip length.");
        require(profiles[username].owner == address(0), "Username already registered on the blockchain.");

        profiles[username] = Profile(pubKey, ipAddr, msg.sender);
        userList.push(username);
        emit ProfileRegistered(username, pubKey, ipAddr, ethAddr);
    }

    function updateIP(string memory username, string memory ipAddr) public {
        require(profiles[username].owner == msg.sender, "Only the profile owner can update it.");
        require(bytes(ipAddr).length <= MAX_IP, "Bad ip length.");
        profiles[username].ipAddr = ipAddr;
        emit IPUpdated(username, ipAddr);
    }

    function getPublicKey(string memory username) public view returns (string memory) {
        require(profiles[username].owner != address(0), "Operator not found in the blockchain registry.");
        return profiles[username].pubKey;
    }

    function getIPAddress(string memory username) public view returns (string memory) {
        return profiles[username].ipAddr;
    }

    function getEthAddress(string memory username) public view returns (address) {
        return profiles[username].owner;
    }

    function getAllUsers() public view returns (string[] memory) {
        return userList;
    }

    function isUserRegistered(string memory username) public view returns (bool) {
        return profiles[username].owner != address(0);
    }
}
