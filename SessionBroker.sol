// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title SessionBroker
 * @dev On-chain signaling layer for A.N.Sx Vault TCP file transfers.
 *
 * The chain is PUBLIC, so the relay session code is never stored in the clear: the
 * sender posts it RSA-OAEP-encrypted to the recipient's public key (`encryptedCode`).
 * Observers see only who pinged whom and when. `sender` is msg.sender, so receivers can
 * verify it against ANSXRegistry instead of trusting the free-text `senderUsername`.
 *
 * Flow:
 *   1. Sender calls postOffer(recipient_eth_addr, senderUsername, encryptedCode, ttl)
 *   2. OfferPosted event fires — receiver's app detects it within 5s via polling
 *   3. Receiver's app auto-connects to TCP relay using sessionCode
 *   4. Receiver calls claimOffer() to mark consumed on-chain (prevents replay)
 *
 * Deployed on Polygon Amoy testnet.
 */
contract SessionBroker {

    struct Offer {
        address sender;
        string  senderUsername;
        bytes   encryptedCode;
        uint256 expiry;
        bool    claimed;
    }

    // recipient ETH address → list of incoming offers
    mapping(address => Offer[]) private _inbox;

    event OfferPosted(
        address indexed recipient,
        address indexed sender,
        string          senderUsername,
        uint256         expiry
    );
    event OfferClaimed(address indexed recipient, uint256 offerIndex);

    /**
     * @dev Post a session offer to a recipient's on-chain inbox.
     * @param recipient     Receiver's Ethereum address (from ANSXRegistry).
     * @param senderUsername Human-readable ANSX username of the sender.
     * @param encryptedCode RSA-OAEP(recipient pubkey, 6-char relay session code).
     * @param ttlSeconds    Time-to-live in seconds (default 300 = 5 minutes).
     */
    function postOffer(
        address recipient,
        string  calldata senderUsername,
        bytes   calldata encryptedCode,
        uint256 ttlSeconds
    ) external {
        require(recipient != address(0), "Invalid recipient");
        require(encryptedCode.length > 0 && encryptedCode.length <= 1024, "Bad ciphertext length");
        require(bytes(senderUsername).length > 0 && bytes(senderUsername).length <= 32, "Bad username length");
        require(_inbox[recipient].length < 256, "Recipient inbox full");
        require(ttlSeconds > 0 && ttlSeconds <= 3600, "TTL must be 1-3600 seconds");

        _inbox[recipient].push(Offer({
            sender:         msg.sender,
            senderUsername: senderUsername,
            encryptedCode:  encryptedCode,
            expiry:         block.timestamp + ttlSeconds,
            claimed:        false
        }));

        emit OfferPosted(recipient, msg.sender, senderUsername, block.timestamp + ttlSeconds);
    }

    /**
     * @dev Get all offers for a recipient (including expired/claimed for history).
     */
    function getOffers(address recipient) external view returns (Offer[] memory) {
        return _inbox[recipient];
    }

    /**
     * @dev Get count of offers for a recipient.
     */
    function getOfferCount(address recipient) external view returns (uint256) {
        return _inbox[recipient].length;
    }

    /**
     * @dev Get a single offer by index.
     */
    function getOffer(address recipient, uint256 index)
        external view
        returns (
            address sender,
            string memory senderUsername,
            bytes memory encryptedCode,
            uint256 expiry,
            bool claimed
        )
    {
        Offer storage o = _inbox[recipient][index];
        return (o.sender, o.senderUsername, o.encryptedCode, o.expiry, o.claimed);
    }

    /**
     * @dev Mark an offer as claimed. Only the recipient can claim their own offers.
     */
    function claimOffer(uint256 index) external {
        Offer storage o = _inbox[msg.sender][index];
        require(!o.claimed, "Offer already claimed");
        require(block.timestamp <= o.expiry, "Offer has expired");
        o.claimed = true;
        emit OfferClaimed(msg.sender, index);
    }

    /**
     * @dev Convenience: get only active (unclaimed, unexpired) offers.
     * Returns parallel arrays: indices, sender addresses, claimed usernames, encrypted codes, expiries.
     */
    function getActiveOffers(address recipient)
        external view
        returns (
            uint256[] memory indices,
            address[] memory senders,
            string[]  memory senderUsernames,
            bytes[]   memory encryptedCodes,
            uint256[] memory expiries
        )
    {
        Offer[] storage all = _inbox[recipient];
        uint256 count = 0;

        // Count active
        for (uint256 i = 0; i < all.length; i++) {
            if (!all[i].claimed && block.timestamp <= all[i].expiry) {
                count++;
            }
        }

        indices         = new uint256[](count);
        senders         = new address[](count);
        senderUsernames = new string[](count);
        encryptedCodes  = new bytes[](count);
        expiries        = new uint256[](count);

        uint256 j = 0;
        for (uint256 i = 0; i < all.length; i++) {
            if (!all[i].claimed && block.timestamp <= all[i].expiry) {
                indices[j]         = i;
                senders[j]         = all[i].sender;
                senderUsernames[j] = all[i].senderUsername;
                encryptedCodes[j]  = all[i].encryptedCode;
                expiries[j]        = all[i].expiry;
                j++;
            }
        }
    }
}
