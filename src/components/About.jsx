import React from 'react';
import { motion } from 'framer-motion';

const skills = [
    "React", "JavaScript (ES6+)", "Tailwind CSS", "Node.js",
    "TypeScript", "Framer Motion", "Git", "UI/UX Design"
];

const About = () => {
    return (
        <section id="about" className="py-20 bg-neutral-950 relative overflow-hidden">
            <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    whileInView={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.6 }}
                    viewport={{ once: true }}
                >
                    <div className="mb-12">
                        <h2 className="text-sm font-bold tracking-widest text-blue-500 uppercase mb-2">About Me</h2>
                        <h3 className="text-3xl md:text-5xl font-['Space_Grotesk'] font-bold text-white">
                            Passionate about creating <br />
                            intuitive <span className="text-purple-500">digital experiences</span>.
                        </h3>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-12">
                        <div>
                            <p className="text-gray-400 text-lg leading-relaxed mb-6">
                                I am a creative developer who loves to bridge the gap between design and technology.
                                With a strong foundation in modern web development, I build fast, accessible, and visually stunning interactive websites.
                            </p>
                            <p className="text-gray-400 text-lg leading-relaxed">
                                When I'm not coding, you can find me exploring new design trends,
                                experimenting with 3D graphics, or contributing to open-source projects.
                            </p>
                        </div>

                        <div>
                            <h4 className="text-xl font-bold text-white mb-6">Technical Skills</h4>
                            <div className="flex flex-wrap gap-3">
                                {skills.map((skill, index) => (
                                    <motion.span
                                        key={skill}
                                        initial={{ opacity: 0, scale: 0.8 }}
                                        whileInView={{ opacity: 1, scale: 1 }}
                                        transition={{ delay: index * 0.05, duration: 0.3 }}
                                        viewport={{ once: true }}
                                        className="px-4 py-2 bg-neutral-900 border border-neutral-800 rounded-full text-gray-300 text-sm hover:border-purple-500 hover:text-white transition-colors cursor-default"
                                    >
                                        {skill}
                                    </motion.span>
                                ))}
                            </div>
                        </div>
                    </div>
                </motion.div>
            </div>
        </section>
    );
};

export default About;
