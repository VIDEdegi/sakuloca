// Standalone verifier built against the same locked minisign-verify crate as
// the updater. Arguments: decoded public-key text, decoded signature, artifact.
use minisign_verify::{PublicKey, Signature};
use std::{env, fs};

fn main() {
    let args: Vec<String> = env::args().collect();
    assert_eq!(args.len(), 4, "public-key signature artifact");
    let key = PublicKey::from_file(&args[1]).expect("invalid public key");
    let signature = Signature::from_file(&args[2]).expect("invalid signature");
    let mut data = fs::read(&args[3]).expect("artifact not readable");
    key.verify(&data, &signature, false).expect("signature rejected");
    data[0] ^= 1;
    assert!(key.verify(&data, &signature, false).is_err(), "tampered artifact accepted");
    println!("Verified artifact and trusted comment; tampered copy rejected.");
}
