import CoreMedia

/// Fotogrammi H.264 dal PC -> campioni per il decodificatore hardware dell'iPad.
/// Il PC manda la configurazione (record avcC con SPS e PPS) e poi i fotogrammi in formato AVCC
/// (ogni unità NAL preceduta dalla sua lunghezza): è proprio il formato che CoreMedia vuole.
enum H264 {
    static func formatDescription(avcC: Data) -> CMVideoFormatDescription? {
        let b = [UInt8](avcC)
        guard b.count > 7 else { return nil }
        let nalLength = Int32(b[4] & 0x03) + 1
        var sets: [[UInt8]] = []
        var i = 5

        func readSets(count: Int) -> Bool {
            for _ in 0..<count {
                guard i + 2 <= b.count else { return false }
                let len = Int(b[i]) << 8 | Int(b[i + 1])
                i += 2
                guard i + len <= b.count, len > 0 else { return false }
                sets.append(Array(b[i..<(i + len)]))
                i += len
            }
            return true
        }

        let spsCount = Int(b[i] & 0x1F)
        i += 1
        guard readSets(count: spsCount) else { return nil }  // SPS
        guard i < b.count else { return nil }
        let ppsCount = Int(b[i])
        i += 1
        guard readSets(count: ppsCount), sets.count >= 2 else { return nil }

        // puntatori validi tutti insieme per la chiamata a CoreMedia
        let buffers = sets.map { s -> UnsafeMutablePointer<UInt8> in
            let p = UnsafeMutablePointer<UInt8>.allocate(capacity: s.count)
            p.initialize(from: s, count: s.count)
            return p
        }
        defer { buffers.forEach { $0.deallocate() } }
        let pointers = buffers.map { UnsafePointer($0) }
        let sizes = sets.map { $0.count }
        var format: CMFormatDescription?
        let status = CMVideoFormatDescriptionCreateFromH264ParameterSets(
            allocator: kCFAllocatorDefault, parameterSetCount: sets.count,
            parameterSetPointers: pointers, parameterSetSizes: sizes,
            nalUnitHeaderLength: nalLength, formatDescriptionOut: &format)
        return status == noErr ? format : nil
    }

    static func sampleBuffer(_ data: Data, format: CMFormatDescription) -> CMSampleBuffer? {
        let count = data.count
        guard count > 0 else { return nil }
        var block: CMBlockBuffer?
        var status = CMBlockBufferCreateWithMemoryBlock(
            allocator: kCFAllocatorDefault, memoryBlock: nil, blockLength: count,
            blockAllocator: kCFAllocatorDefault, customBlockSource: nil, offsetToData: 0,
            dataLength: count, flags: 0, blockBufferOut: &block)
        guard status == kCMBlockBufferNoErr, let block else { return nil }
        status = data.withUnsafeBytes { raw in
            CMBlockBufferReplaceDataBytes(with: raw.baseAddress!, blockBuffer: block,
                                          offsetIntoDestination: 0, dataLength: count)
        }
        guard status == kCMBlockBufferNoErr else { return nil }

        var sample: CMSampleBuffer?
        var size = count
        status = CMSampleBufferCreateReady(
            allocator: kCFAllocatorDefault, dataBuffer: block, formatDescription: format,
            sampleCount: 1, sampleTimingEntryCount: 0, sampleTimingArray: nil,
            sampleSizeEntryCount: 1, sampleSizeArray: &size, sampleBufferOut: &sample)
        guard status == noErr, let sample else { return nil }

        // mostra appena decodificato, senza orari di riproduzione: ritardo minimo
        if let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: true),
           CFArrayGetCount(attachments) > 0 {
            let dict = unsafeBitCast(CFArrayGetValueAtIndex(attachments, 0), to: CFMutableDictionary.self)
            CFDictionarySetValue(dict,
                                 Unmanaged.passUnretained(kCMSampleAttachmentKey_DisplayImmediately).toOpaque(),
                                 Unmanaged.passUnretained(kCFBooleanTrue).toOpaque())
        }
        return sample
    }
}
