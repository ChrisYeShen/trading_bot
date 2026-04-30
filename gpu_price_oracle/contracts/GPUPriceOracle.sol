// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title GPUPriceOracle
 * @notice On-chain oracle storing GPU hardware sale prices and cloud rental prices.
 *         Authorized feeders push aggregated price data; anyone can read it.
 *
 * Price units
 *   hardware_price_usd  – USD cents (e.g. 150000 = $1,500.00)
 *   rental_price_usd    – USD millicents per GPU-hour (e.g. 30000 = $0.30/hr)
 */
contract GPUPriceOracle is Ownable {
    struct GPUPrice {
        uint256 hardwarePriceUsdCents;  // spot / used market price
        uint256 rentalPriceUsdMilliCentsPerHour;
        uint256 updatedAt;              // block.timestamp
        uint256 numSources;             // how many data sources were aggregated
    }

    // gpu model id (keccak256 of canonical name) => latest price
    mapping(bytes32 => GPUPrice) public prices;

    // human-readable name => id
    mapping(string => bytes32) public gpuIds;
    string[] public gpuList;

    // addresses allowed to push price updates
    mapping(address => bool) public feeders;

    event PriceUpdated(
        bytes32 indexed gpuId,
        string gpuName,
        uint256 hardwarePriceUsdCents,
        uint256 rentalPriceUsdMilliCentsPerHour,
        uint256 numSources,
        address indexed feeder
    );

    event FeederUpdated(address indexed feeder, bool authorized);

    modifier onlyFeeder() {
        require(feeders[msg.sender] || msg.sender == owner(), "not authorized feeder");
        _;
    }

    constructor() Ownable(msg.sender) {
        feeders[msg.sender] = true;
    }

    // ── Admin ────────────────────────────────────────────────────────────────

    function setFeeder(address feeder, bool authorized) external onlyOwner {
        feeders[feeder] = authorized;
        emit FeederUpdated(feeder, authorized);
    }

    // ── Write ────────────────────────────────────────────────────────────────

    /**
     * @notice Push a price update for a single GPU model.
     * @param gpuName          Canonical GPU name, e.g. "NVIDIA RTX 4090"
     * @param hardwareUsdCents Hardware spot price in USD cents
     * @param rentalMilliCents Rental price in USD milli-cents per GPU-hour
     * @param numSources       Number of data sources aggregated by the feeder
     */
    function updatePrice(
        string calldata gpuName,
        uint256 hardwareUsdCents,
        uint256 rentalMilliCents,
        uint256 numSources
    ) external onlyFeeder {
        bytes32 id = keccak256(abi.encodePacked(gpuName));
        if (gpuIds[gpuName] == bytes32(0)) {
            gpuIds[gpuName] = id;
            gpuList.push(gpuName);
        }
        prices[id] = GPUPrice({
            hardwarePriceUsdCents: hardwareUsdCents,
            rentalPriceUsdMilliCentsPerHour: rentalMilliCents,
            updatedAt: block.timestamp,
            numSources: numSources
        });
        emit PriceUpdated(id, gpuName, hardwareUsdCents, rentalMilliCents, numSources, msg.sender);
    }

    /**
     * @notice Batch update multiple GPU prices in one transaction.
     */
    function updatePriceBatch(
        string[] calldata gpuNames,
        uint256[] calldata hardwareUsdCents,
        uint256[] calldata rentalMilliCents,
        uint256[] calldata numSourcesArr
    ) external onlyFeeder {
        require(
            gpuNames.length == hardwareUsdCents.length &&
            gpuNames.length == rentalMilliCents.length &&
            gpuNames.length == numSourcesArr.length,
            "array length mismatch"
        );
        for (uint256 i = 0; i < gpuNames.length; i++) {
            bytes32 id = keccak256(abi.encodePacked(gpuNames[i]));
            if (gpuIds[gpuNames[i]] == bytes32(0)) {
                gpuIds[gpuNames[i]] = id;
                gpuList.push(gpuNames[i]);
            }
            prices[id] = GPUPrice({
                hardwarePriceUsdCents: hardwareUsdCents[i],
                rentalPriceUsdMilliCentsPerHour: rentalMilliCents[i],
                updatedAt: block.timestamp,
                numSources: numSourcesArr[i]
            });
            emit PriceUpdated(
                id,
                gpuNames[i],
                hardwareUsdCents[i],
                rentalMilliCents[i],
                numSourcesArr[i],
                msg.sender
            );
        }
    }

    // ── Read ─────────────────────────────────────────────────────────────────

    function getPrice(string calldata gpuName)
        external
        view
        returns (
            uint256 hardwarePriceUsdCents,
            uint256 rentalPriceUsdMilliCentsPerHour,
            uint256 updatedAt,
            uint256 numSources
        )
    {
        GPUPrice memory p = prices[keccak256(abi.encodePacked(gpuName))];
        require(p.updatedAt > 0, "no price for this GPU");
        return (p.hardwarePriceUsdCents, p.rentalPriceUsdMilliCentsPerHour, p.updatedAt, p.numSources);
    }

    function getAllGPUs() external view returns (string[] memory) {
        return gpuList;
    }

    function gpuCount() external view returns (uint256) {
        return gpuList.length;
    }
}
