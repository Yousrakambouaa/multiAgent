using UnityEngine;

public class AudioManager : MonoBehaviour
{
    public AudioClip backgroundMusic;
    public AudioSource audioSource;

    void Start() {
        audioSource.clip = backgroundMusic;
        audioSource.Play();
    }

    public void PlaySound(AudioClip clip) {
        audioSource.PlayOneShot(clip);
    }
}